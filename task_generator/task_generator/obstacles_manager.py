import math
import random
from typing import Union
import re
import yaml
import os
import warnings
import numpy as np
from flatland_msgs.srv import DeleteModel, DeleteModelRequest
from flatland_msgs.srv import SpawnModel, SpawnModelRequest
from flatland_msgs.srv import MoveModel, MoveModelRequest
from flatland_msgs.srv import StepWorld
from nav_msgs.msg import OccupancyGrid
from geometry_msgs.msg import Pose2D, Twist, Point
from pedsim_srvs.srv import SpawnPeds
from pedsim_srvs.srv import SpawnObstacle,SpawnObstacleRequest
from pedsim_srvs.srv import MovePeds,MovePedsRequest
from pedsim_srvs.srv import SpawnZeroAgents
from pedsim_msgs.msg import Ped
from pedsim_msgs.msg import ZeroAgent
from pedsim_msgs.msg import LineObstacle
from pedsim_msgs.msg import LineObstacles
from std_srvs.srv import SetBool, Empty
import rospy
import rospkg
import shutil
import time
from .utils import *

class ObstaclesManager:
    """
    A manager class using flatland provided services to spawn, move and delete obstacles.
    """

    def __init__(self, ns: str, map_: OccupancyGrid):
        """
        Args:
            map_ (OccupancyGrid):
            plugin_name: The name of the plugin which is used to control the movement of the obstacles, Currently we use "RandomMove" for training and Tween2 for evaluation.
                The Plugin Tween2 can move the the obstacle along a trajectory which can be assigned by multiple waypoints with a constant velocity.Defaults to "RandomMove".
        """
        self.ns = ns
        self.ns_prefix = "/" if ns == '' else "/"+ns+"/"

        # a list of publisher to move the obstacle to the start pos.
        self._move_all_obstacles_start_pos_pubs = []

        # setup proxy to handle  services provided by flatland
        rospy.wait_for_service(f'{self.ns_prefix}move_model', timeout=20)
        rospy.wait_for_service(f'{self.ns_prefix}delete_model', timeout=20)
        rospy.wait_for_service(f'{self.ns_prefix}spawn_model', timeout=20)
        rospy.wait_for_service(f'{self.ns_prefix}pedsim_simulator/remove_all_peds', timeout=20)
        rospy.wait_for_service(f'{self.ns_prefix}pedsim_simulator/remove_all_polygons', timeout=20)
        rospy.wait_for_service(f'{self.ns_prefix}pedsim_simulator/add_obstacle', timeout=20)
        rospy.wait_for_service(f'{self.ns_prefix}pedsim_simulator/add_polygon', timeout=20)
        rospy.wait_for_service(f'{self.ns_prefix}pedsim_simulator/respawn_peds' , timeout=20)
        rospy.wait_for_service(f'{self.ns_prefix}pedsim_simulator/spawn_ped' , timeout=20)
        rospy.wait_for_service(f'{self.ns_prefix}pedsim_simulator/move_peds' , timeout=20)
        # allow for persistent connections to services
        self._srv_move_model = rospy.ServiceProxy(
            f'{self.ns_prefix}move_model', MoveModel, persistent=True)
        self._srv_delete_model = rospy.ServiceProxy(
            f'{self.ns_prefix}delete_model', DeleteModel, persistent=True)
        self._srv_spawn_model = rospy.ServiceProxy(
            f'{self.ns_prefix}spawn_model', SpawnModel, persistent=True)

        self.__spawn_ped_srv = rospy.ServiceProxy(
            f'{self.ns_prefix}pedsim_simulator/spawn_ped', SpawnPeds, persistent=True)
        self.__respawn_peds_srv = rospy.ServiceProxy(
            f'{self.ns_prefix}pedsim_simulator/respawn_peds' , SpawnPeds, persistent=True)
        self.__remove_all_peds_srv = rospy.ServiceProxy(
            f'{self.ns_prefix}pedsim_simulator/remove_all_peds' , SetBool, persistent=True)
        self.__remove_all_polygons_srv = rospy.ServiceProxy(
            f'{self.ns_prefix}pedsim_simulator/remove_all_polygons' , SetBool, persistent=True)
        self.__add_obstacle_srv = rospy.ServiceProxy(
            f'{self.ns_prefix}pedsim_simulator/add_obstacle' ,SpawnObstacle, persistent=True)
        self.add_polygon_service_ = rospy.ServiceProxy(
            f'{self.ns_prefix}pedsim_simulator/add_polygon' ,SpawnZeroAgents, persistent=True)
        self.__move_peds_srv = rospy.ServiceProxy(
            f'{self.ns_prefix}pedsim_simulator/move_peds' ,MovePeds, persistent=True)

        self.update_map(map_)
        self.obstacle_name_list = []
        self._obstacle_name_prefix = 'obstacle'

        #ped names and waypoints
        self.__peds=[]
        self.human_waypoints = []

        #tell the pedsim the map border
        self._add_map_border_into_pedsim()
        # remove all existing obstacles generated before create an instance of this class
        self.remove_obstacles()

        #construct maze ?
        self.useMaze= False
        self.s_wall_list=[]
        self.l_wall_list=[]
        if self.useMaze:
            self.build_maze()
        # human group pattern
        self.circlePattern = rospy.get_param("/useCirclePattern")
        self.mixRate = rospy.get_param("/mixRate")

    def update_map(self, new_map: OccupancyGrid):
        self.map = new_map
        # a tuple stores the indices of the non-occupied spaces. format ((y,....),(x,...)
        self._free_space_indices = generate_freespace_indices(self.map)

    def register_obstacles(self, 
    num_obstacles: int, 
    model_yaml_file_path: str, 
    start_pos: list=[] , 
    vertices:np.ndarray=np.array([]), 
    type_obstacle:str='dynamic'):
        """register the obstacles defined by a yaml file and request flatland to respawn the them.

        Args:
            num_obstacles (string): the number of the obstacle instance to be created.
            model_yaml_file_path (string or None): model file path. it must be absolute path!
            start_pos (list)  a three-elementary list of empty list, if it is empty, the obstacle will be moved to the
                outside of the map.

        Raises:
            Exception:  Rospy.ServiceException(
                f" failed to register obstacles")

        Returns:
            self.
        """
        assert os.path.isabs(
            model_yaml_file_path), "The yaml file path must be absolute path, otherwise flatland can't find it"

        # the name of the model yaml file have the format {model_name}.model.yaml
        # we added environments's namespace as the prefix in the model_name to make sure the every environment has it's own temporary model file
        model_name = os.path.basename(model_yaml_file_path).split('.')[0]
        # But we don't want to keep it in the name of the topic otherwise it won't be easy to visualize them in riviz
        # print(model_name)
        model_name = model_name.replace(self.ns,'')        
        name_prefix = self._obstacle_name_prefix + '_' + model_name
        if type_obstacle == 'human':
            # self.__remove_all_peds()
            if self.circlePattern:
                self.register_static_walls()
            self.spawn_random_peds_in_world(num_obstacles)
        elif type_obstacle == 'polygon':
            # self.__remove_all_polygons()
            if num_obstacles>0:
                self.spawn_random_polygons_in_world(num_obstacles)
        else:
            count_same_type = sum(
                1 if obstacle_name.startswith(name_prefix) else 0
                for obstacle_name in self.obstacle_name_list)

            for instance_idx in range(count_same_type, count_same_type + num_obstacles):
                max_num_try = 2
                i_curr_try = 0
                while i_curr_try < max_num_try:
                    spawn_request = SpawnModelRequest()
                    spawn_request.yaml_path = model_yaml_file_path
                    spawn_request.name = f'{name_prefix}_{instance_idx:02d}'
                    spawn_request.ns = rospy.get_namespace()
                    # x, y, theta = get_random_pos_on_map(self._free_space_indices, self.map,)
                    # set the postion of the obstacle out of the map to hidden them
                    if len(start_pos) == 0:
                        x = self.map.info.origin.position.x - 3 * \
                            self.map.info.resolution * self.map.info.height
                        y = self.map.info.origin.position.y - 3 * \
                            self.map.info.resolution * self.map.info.width
                        theta = theta = random.uniform(-math.pi, math.pi)
                    else:
                        assert len(start_pos) == 3
                        x = start_pos[0]
                        y = start_pos[1]
                        theta = start_pos[2]
                    spawn_request.pose.x = x
                    spawn_request.pose.y = y
                    spawn_request.pose.theta = theta
                    # try to call service
                    response = self._srv_spawn_model.call(spawn_request)
                    if not response.success:  # if service not succeeds, do something and redo service
                        rospy.logwarn(
                            f"({self.ns}) spawn object {spawn_request.name} failed! trying again... [{i_curr_try+1}/{max_num_try} tried]")
                        rospy.logwarn(response.message)
                        i_curr_try += 1
                    else:
                        self.obstacle_name_list.append(spawn_request.name)
                        #tell the info of polygon obstacles to pedsim
                        add_pedsim_srv=SpawnObstacleRequest()
                        size=vertices.shape[0]
                        for i in range(size):
                            lineObstacle=LineObstacle()
                            lineObstacle.start.x,lineObstacle.start.y=vertices[i,0],vertices[i,1]
                            lineObstacle.end.x,lineObstacle.end.y=vertices[(i+1)%size,0],vertices[(i+1)%size,1]
                            add_pedsim_srv.staticObstacles.obstacles.append(lineObstacle)
                        self.__add_obstacle_srv.call(add_pedsim_srv)
                        break
                if i_curr_try == max_num_try:
                    raise rospy.ServiceException(f"({self.ns}) failed to register obstacles")
        return self

    def register_random_obstacles(self, num_obstacles: int, p_dynamic=1):
        """register static or dynamic obstacles.

        Args:
            num_obstacles (int): number of the obstacles
            p_dynamic(float): the possibility of a obstacle is dynamic
            linear_velocity: the maximum linear velocity
        """
        num_dynamic_obstalces = int(num_obstacles*p_dynamic)
        max_linear_velocity = rospy.get_param("/obs_vel")
        # self.register_random_dynamic_obstacles(
        #     num_dynamic_obstalces, max_linear_velocity)
        self.register_human(num_dynamic_obstalces)
        self.register_random_static_obstacles(
            num_obstacles-num_dynamic_obstalces)
        rospy.loginfo(
            f"Registed {num_dynamic_obstalces} dynamic obstacles and {num_obstacles-num_dynamic_obstalces} static obstacles")

    def register_random_dynamic_obstacles(self, num_obstacles: int, linear_velocity=0.3, angular_velocity_max=math.pi/6, min_obstacle_radius=0.1, max_obstacle_radius=0.4):
        """register dynamic obstacles with circle shape.

        Args:
            num_obstacles (int): number of the obstacles.
            linear_velocity (float, optional):  the constant linear velocity of the dynamic obstacle.
            angular_velocity_max (float, optional): the maximum angular verlocity of the dynamic obstacle.
                When the obstacle's linear velocity is too low(because of the collision),we will apply an
                angular verlocity which is sampled from [-angular_velocity_max,angular_velocity_max] to the it to help it better escape from the "freezing" satuation.
            min_obstacle_radius (float, optional): the minimum radius of the obstacle. Defaults to 0.5.
            max_obstacle_radius (float, optional): the maximum radius of the obstacle. Defaults to 0.5.
        """
        for _ in range(num_obstacles):
            model_path = self._generate_random_obstacle_yaml(
                True, linear_velocity=linear_velocity, angular_velocity_max=angular_velocity_max,
                min_obstacle_radius=min_obstacle_radius, max_obstacle_radius=max_obstacle_radius)
            self.register_obstacles(1, model_path)
            os.remove(model_path)

    def register_human(self, num_obstacles: int):
        """register dynamic obstacles human.

        Args:
            num_obstacles (int): number of the obstacles.
        """
        model_path = os.path.join(rospkg.RosPack().get_path(
        'simulator_setup'), 'dynamic_obstacles/person_two_legged.model.yaml')
        self.num_humans=num_obstacles
        self.register_obstacles(num_obstacles+self.num_polygons, model_path, type_obstacle='human')

    def register_random_static_obstacles(self, num_obstacles: int, num_vertices_min=3, num_vertices_max=5, min_obstacle_radius=0.5, max_obstacle_radius=2):
        """register static obstacles with polygon shape.

        Args:
            num_obstacles (int): number of the obstacles.
            num_vertices_min (int, optional): the minimum number of the vertices . Defaults to 3.
            num_vertices_max (int, optional): the maximum number of the vertices. Defaults to 6.
            min_obstacle_radius (float, optional): the minimum radius of the obstacle. Defaults to 0.5.
            max_obstacle_radius (float, optional): the maximum radius of the obstacle. Defaults to 2.
        """
        for _ in range(num_obstacles):
            num_vertices = random.randint(num_vertices_min, num_vertices_max)
            model_path = self._generate_random_obstacle_yaml(
                False, num_vertices=num_vertices, min_obstacle_radius=min_obstacle_radius, max_obstacle_radius=max_obstacle_radius)
            self.register_obstacles(1, model_path)
            os.remove(model_path)


    def register_static_obstacle_polygon(self, vertices: np.ndarray):
        """register static obstacle with polygon shape

        Args:
            verticies (np.ndarray): a two-dimensional numpy array, each row has two elements
        """
        assert vertices.ndim == 2 and vertices.shape[0] >= 3 and vertices.shape[1] == 2
        model_path, start_pos = self._generate_static_obstacle_polygon_yaml(
            vertices)
        self.register_obstacles(1, model_path, start_pos)
        os.remove(model_path)

    def register_static_obstacle_circle(self, x, y, circle):
        model_path = self._generate_static_obstacle_circle_yaml(circle)
        self.register_obstacles(1, model_path, [x, y, 0])
        os.remove(model_path)

    def register_dynamic_obstacle_circle_tween2(self, obstacle_name: str, obstacle_radius: float, linear_velocity: float, start_pos: Pose2D, waypoints: list, is_waypoint_relative: bool = True,  mode: str = "yoyo", trigger_zones: list = []):
        """register dynamic obstacle with circle shape. The trajectory of the obstacle is defined with the help of the plugin "tween2"

        Args:
            obstacle_name (str): the name of the obstacle
            obstacle_radius (float): The radius of the obstacle
            linear_velocity (float): The linear velocity of the obstacle
            start_pos (list): 3-elementary list
            waypoints (list): a list of 3-elementary list
            is_waypoint_relative (bool, optional): a flag to indicate whether the waypoint is relative to the start_pos or not . Defaults to True.
            mode (str, optional): [description]. Defaults to "yoyo".
            trigger_zones (list): a list of 3-elementary, every element (x,y,r) represent a circle zone with the center (x,y) and radius r. if its empty,
                then the dynamic obstacle will keeping moving once it is spawned. Defaults to True.
        """
        model_path, move_to_start_pub = self._generate_dynamic_obstacle_yaml_tween2(
            obstacle_name, obstacle_radius, linear_velocity, waypoints, is_waypoint_relative,  mode, trigger_zones)
        self._move_all_obstacles_start_pos_pubs.append(move_to_start_pub)
        self.register_obstacles(1, model_path, start_pos)
        os.remove(model_path)

    def move_all_obstacles_to_start_pos_tween2(self):
        for move_obstacle_start_pos_pub in self._move_all_obstacles_start_pos_pubs:
            move_obstacle_start_pos_pub.publish(Empty())

    def move_obstacle(self, obstacle_name: str, x: float, y: float, theta: float):
        """move the obstacle to a given position

        Args:
            obstacle_name (str): [description]
            x (float): [description]
            y (float): [description]
            theta (float): [description]
        """

        assert obstacle_name in self.obstacle_name_list, "can't move the obstacle because it has not spawned in the flatland"
        # call service move_model

        srv_request = MoveModelRequest()
        srv_request.name = obstacle_name
        srv_request.pose.x = x
        srv_request.pose.y = y
        srv_request.pose.theta = theta

        self._srv_move_model(srv_request)

    def reset_pos_obstacles_random(self, active_obstacle_rate: float = 1, forbidden_zones: Union[list, None] = None):
        """randomly set the position of all the obstacles. In order to dynamically control the number of the obstacles within the
        map while keep the efficiency. we can set the parameter active_obstacle_rate so that the obstacles non-active will moved to the
        outside of the map

        Args:
            active_obstacle_rate (float): a parameter change the number of the obstacles within the map
            forbidden_zones (list): a list of tuples with the format (x,y,r),where the the obstacles should not be reset.
        """
        active_obstacle_names = random.sample(self.obstacle_name_list, int(
            len(self.obstacle_name_list) * active_obstacle_rate))
        non_active_obstacle_names = set(
            self.obstacle_name_list) - set(active_obstacle_names)

        # non_active obstacles will be moved to outside of the map
        resolution = self.map.info.resolution
        pos_non_active_obstacle = Pose2D()
        pos_non_active_obstacle.x = self.map.info.origin.position.x - \
            resolution * self.map.info.width
        pos_non_active_obstacle.y = self.map.info.origin.position.y - \
            resolution * self.map.info.width

        for obstacle_name in active_obstacle_names:
            move_model_request = MoveModelRequest()
            move_model_request.name = obstacle_name
            # TODO 0.2 is the obstacle radius. it should be set automatically in future.
            move_model_request.pose.x, move_model_request.pose.y, move_model_request.pose.theta = get_random_pos_on_map(
                self._free_space_indices, self.map, 0.2, forbidden_zones)

            self._srv_move_model(move_model_request)

        for non_active_obstacle_name in non_active_obstacle_names:
            move_model_request = MoveModelRequest()
            move_model_request.name = non_active_obstacle_name
            move_model_request.pose = pos_non_active_obstacle
            self._srv_move_model(move_model_request)

    def _generate_dynamic_obstacle_yaml_tween2(self, obstacle_name: str, obstacle_radius: float, linear_velocity: float, waypoints: list, is_waypoint_relative: bool,  mode: str, trigger_zones: list):
        """generate a yaml file in which the movement of the obstacle is controller by the plugin tween2

        Args:
            obstacle_name (str): [description]
            obstacle_radius (float): [description]
            linear_velocity (float): [description]
            is_waypoint_relative (bool): [description]
            waypoints (list): [description]
            mode (str, optional): [description]. Defaults to "yoyo".
            trigger_zones (list): a list of 3-elementary, every element (x,y,r) represent a circle zone with the center (x,y) and radius r. if its empty,
                then the dynamic obstacle will keeping moving once it is spawned. Defaults to True.
        Returns:
            [type]: [description]
        """
        for i, way_point in enumerate(waypoints):
            if len(way_point) != 3:
                raise ValueError(
                    f"ways points must a list of 3-elementary list, However the {i}th way_point is {way_point}")
        tmp_folder_path = os.path.join(rospkg.RosPack().get_path(
            'simulator_setup'), 'tmp_random_obstacles')
        os.makedirs(tmp_folder_path, exist_ok=True)
        tmp_model_name = self.ns+"_dynamic_with_traj.model.yaml"
        yaml_path = os.path.join(tmp_folder_path, tmp_model_name)
        # define body
        body = {}
        body["name"] = "object_with_traj"
        body["type"] = "dynamic"
        body["color"] = [1, 0.2, 0.1, 1.0]
        body["footprints"] = []

        # define footprint
        f = {}
        f["density"] = 1
        f['restitution'] = 0
        f["layers"] = ["all"]
        f["collision"] = 'true'
        f["sensor"] = "false"
        # dynamic obstacles have the shape of circle
        f["type"] = "circle"
        f["radius"] = obstacle_radius

        body["footprints"].append(f)
        # define dict_file
        dict_file = {'bodies': [body], "plugins": []}
        # We added new plugin called RandomMove in the flatland repo
        move_with_traj = {}
        move_with_traj['type'] = 'Tween2'
        move_with_traj['name'] = 'Tween2 Plugin'
        move_with_traj['linear_velocity'] = linear_velocity
        # set the topic name for moving the object to the start point.
        # we can not use the flatland provided service to move the object, othewise the Tween2 will not work properly.
        move_with_traj['move_to_start_pos_topic'] = self.ns_prefix + obstacle_name + \
            '/move_to_start_pos'
        move_to_start_pos_pub = rospy.Publisher(
            move_with_traj['move_to_start_pos_topic'], Empty, queue_size=1)
        move_with_traj['waypoints'] = waypoints
        move_with_traj['is_waypoint_relative'] = is_waypoint_relative
        move_with_traj['mode'] = mode
        move_with_traj['body'] = 'object_with_traj'
        move_with_traj['trigger_zones'] = trigger_zones
        move_with_traj['robot_odom_topic'] = self.ns_prefix + 'odom'
        dict_file['plugins'].append(move_with_traj)

        with open(yaml_path, 'w') as fd:
            yaml.dump(dict_file, fd)
        return yaml_path, move_to_start_pos_pub

    def _generate_static_obstacle_polygon_yaml(self, vertices, i):
        # since flatland  can only config the model by parsing the yaml file, we need to create a file for every random obstacle
        tmp_folder_path = os.path.join(rospkg.RosPack().get_path(
            'simulator_setup'), 'tmp_random_obstacles')
        os.makedirs(tmp_folder_path, exist_ok=True)
        tmp_model_name = self.ns+f"_polygon_static_{i}.model.yaml"
        yaml_path = os.path.join(tmp_folder_path, tmp_model_name)
        # define body
        body = {}
        body["name"] = "static_object"
        # calculate center of the obstacle
        obstacle_center = vertices.mean(axis=0)
        # convert to local coordinate system
        vertices = vertices - obstacle_center
        # convert to x,y,theta
        obstacle_center = obstacle_center.tolist()
        obstacle_center.append(0.0)

        body["type"] = "static"
        body["color"] = [0.2, 0.8, 0.2, 0.75]
        body["footprints"] = []

        # define footprint
        f = {}
        f["density"] = 1
        f['restitution'] = 0
        f["layers"] = ["all"]
        f["collision"] = 'true'
        f["sensor"] = "false"
        f["type"] = "polygon"
        f["points"] = vertices.astype(np.float).tolist()

        body["footprints"].append(f)
        # define dict_file
        dict_file = {'bodies': [body]}
        with open(yaml_path, 'w') as fd:
            yaml.dump(dict_file, fd)
        return yaml_path, obstacle_center

    def _generate_static_obstacle_circle_yaml(self, radius):
        # since flatland  can only config the model by parsing the yaml file, we need to create a file for every random obstacle
        tmp_folder_path = os.path.join(rospkg.RosPack().get_path(
            'simulator_setup'), 'tmp_random_obstacles')
        os.makedirs(tmp_folder_path, exist_ok=True)
        tmp_model_name = self.ns+"_circle_static.model.yaml"
        yaml_path = os.path.join(tmp_folder_path, tmp_model_name)
        # define body
        body = {}
        body["name"] = "static_object"
        body["type"] = "static"
        body["color"] = [0.2, 0.8, 0.2, 0.75]
        body["footprints"] = []

        # define footprint
        f = {}
        f["density"] = 1
        f['restitution'] = 0
        f["layers"] = ["all"]
        f["collision"] = 'true'
        f["sensor"] = "false"
        f["type"] = "circle"
        f["radius"] = radius

        body["footprints"].append(f)
        # define dict_file
        dict_file = {'bodies': [body]}
        with open(yaml_path, 'w') as fd:
            yaml.dump(dict_file, fd)
        return yaml_path

    def setForbidden_zones(self, forbidden_zones: Union[list, None] = None):
        """ set the forbidden areas for spawning obstacles
        """
        self.forbidden_zones=forbidden_zones 

    def _generate_random_obstacle_yaml(self,
                                       is_dynamic=False,
                                       linear_velocity=0.3,
                                       angular_velocity_max=math.pi/4,
                                       num_vertices=3,
                                       min_obstacle_radius=0.5,
                                       max_obstacle_radius=1.5):
        """generate a yaml file describing the properties of the obstacle.
        The dynamic obstacles have the shape of circle,which moves with a constant linear velocity and angular_velocity_max

        and the static obstacles have the shape of polygon.

        Args:
            is_dynamic (bool, optional): a flag to indicate generate dynamic or static obstacle. Defaults to False.
            linear_velocity (float): the constant linear velocity of the dynamic obstacle. Defaults to 1.5.
            angular_velocity_max (float): the maximum angular velocity of the dynamic obstacle. Defaults to math.pi/4.
            num_vertices (int, optional): the number of vetices, only used when generate static obstacle . Defaults to 3.
            min_obstacle_radius (float, optional): Defaults to 0.5.
            max_obstacle_radius (float, optional): Defaults to 1.5.
        """

        # since flatland  can only config the model by parsing the yaml file, we need to create a file for every random obstacle
        tmp_folder_path = os.path.join(rospkg.RosPack().get_path(
            'simulator_setup'), 'tmp_random_obstacles')
        os.makedirs(tmp_folder_path, exist_ok=True)
        if is_dynamic:
            tmp_model_name = self.ns+"_random_dynamic.model.yaml"
        else:
            tmp_model_name = self.ns+"_random_static.model.yaml"
        yaml_path = os.path.join(tmp_folder_path, tmp_model_name)
        # define body
        body = {}
        body["name"] = "random"
        body["pose"] = [0, 0, 0]
        if is_dynamic:
            body["type"] = "dynamic"
        else:
            body["type"] = "static"
        body["color"] = [1, 0.2, 0.1, 1.0]  # [0.2, 0.8, 0.2, 0.75]
        body["footprints"] = []

        # define footprint
        f = {}
        f["density"] = 1
        f['restitution'] = 1
        f["layers"] = ["all"]
        f["collision"] = 'true'
        f["sensor"] = "false"
        # dynamic obstacles have the shape of circle
        if is_dynamic:
            f["type"] = "circle"
            f["radius"] = random.uniform(
                min_obstacle_radius, max_obstacle_radius)
        else:
            f["type"] = "polygon"
            f["points"] = []
            # random_num_vert = random.randint(
            #     min_obstacle_vert, max_obstacle_vert)
            radius = random.uniform(
                min_obstacle_radius, max_obstacle_radius)
            # When we send the request to ask flatland server to respawn the object with polygon, it will do some checks
            # one important assert is that the minimum distance should be above this value
            # https://github.com/erincatto/box2d/blob/75496a0a1649f8ee6d2de6a6ab82ee2b2a909f42/include/box2d/b2_common.h#L65
            POINTS_MIN_DIST = 0.005*1.1

            def min_dist_check_passed(points):
                points_1_x_2 = points[None, ...]
                points_x_1_2 = points[:, None, :]
                points_dist = ((points_1_x_2-points_x_1_2)
                               ** 2).sum(axis=2).squeeze()
                np.fill_diagonal(points_dist, 1)
                min_dist = points_dist.min()
                return min_dist > POINTS_MIN_DIST
            points = None
            while points is None:
                angles = 2*np.pi*np.random.random(num_vertices)
                points = np.array([np.cos(angles), np.sin(angles)]).T
                if not min_dist_check_passed(points):
                    points = None
            f['points'] = points.tolist()

        body["footprints"].append(f)
        # define dict_file
        dict_file = {'bodies': [body], "plugins": []}
        if is_dynamic:
            # We added new plugin called RandomMove in the flatland repo
            random_move = {}
            random_move['type'] = 'RandomMove'
            random_move['name'] = 'RandomMove Plugin'
            random_move['linear_velocity'] = linear_velocity
            random_move['angular_velocity_max'] = angular_velocity_max
            random_move['body'] = 'random'
            dict_file['plugins'].append(random_move)

        with open(yaml_path, 'w') as fd:
            yaml.dump(dict_file, fd)
        return yaml_path

    def remove_obstacle(self, name: str):
        if len(self.obstacle_name_list) != 0:
            assert name in self.obstacle_name_list
        srv_request = DeleteModelRequest()
        srv_request.name = name
        response = self._srv_delete_model(srv_request)

        if not response.success:
            """
            raise rospy.ServiceException(
                f"failed to remove the object with the name: {name}! ")
            """
            warnings.warn(
                f"failed to remove the object with the name: {name}!")
        else:
            rospy.logdebug(f"Removed the obstacle with the name {name}")

    def remove_obstacles(self, prefix_names: Union[list, None] = None):
        """remove all the obstacless belong to specific groups.
        Args:
            prefix_names (Union[list,None], optional): a list of group names. if it is None then all obstacles will
                be deleted. Defaults to None.
        """
        if len(self.obstacle_name_list) != 0:
            if prefix_names is None:
                group_names = '.'
                re_pattern = "^(?:" + '|'.join(group_names) + r')\w*'
            else:
                re_pattern = "^(?:" + '|'.join(prefix_names) + r')\w*'
            r = re.compile(re_pattern)
            to_be_removed_obstacles_names = list(
                filter(r.match, self.obstacle_name_list))
            for n in to_be_removed_obstacles_names:
                self.remove_obstacle(n)
            self.obstacle_name_list = list(
                set(self.obstacle_name_list)-set(to_be_removed_obstacles_names))
        else:
            # # it possible that in flatland there are still obstacles remaining when we create an instance of
            # # this class.
            max_tries = 5
            while max_tries > 0:
                # some time the returned topices is not iterable
                try:
                    topics = rospy.get_published_topics()
                    for t in topics:
                        # sometimes the returned topics are very weired!!!!! Maybe a bug of rospy
                            # the format of the topic is (topic_name,message_name)
                            topic_components = t[0].split("/")
                            # like "/.*/"
                            if len(topic_components)<3:
                                continue
                            _,topic_ns,*_,topic_name = topic_components
                            if topic_ns == self.ns and topic_name.startswith(self._obstacle_name_prefix):
                                self.remove_obstacle(topic_name)
                    break
                except Exception as e:
                    max_tries -= 1
                    rospy.logwarn(
                        f"Can not get publised topics, will try more {max_tries} times.")
                    import time
                    time.sleep(1)
            if max_tries == 0:
                rospy.logwarn(
                    "Can not get publised topics with 'rospy.get_published_topics'")
            # pass
    def __respawn_peds(self, peds):
        """
        Spawning pedestrian in the simulation. The type of pedestrian is randomly decided here.
        TODO: the task generator later can decide the number of the agents
        ADULT = 0, CHILD = 1, ROBOT = 2, ELDER = 3,
        ADULT_AVOID_ROBOT = 10, ADULT_AVOID_ROBOT_REACTION_TIME = 11
        :param  start_pos start position of the pedestrian.
        :param  wps waypoints the pedestrian is supposed to walk to.
        :param  id id of the pedestrian.
        """
        srv = SpawnPeds()
        srv.peds = []
        # self.agent_topic_str=''
        for i, ped in enumerate(peds):
            if(i<self.num_humans):
                elements = [0, 1, 3]
                #a c e a c e
                #1 2 3 4.....
                # probabilities = [0.4, 0.3, 0.3] np.random.choice(elements, 1, p=probabilities)[0]
                self.__ped_type=elements[(ped[0]-1)%3]
                if  self.__ped_type==0:
                    # self.agent_topic_str+=f',{self.ns_prefix}pedsim_agent_{ped[0]}/dynamic_human'
                    self.__ped_file=os.path.join(rospkg.RosPack().get_path(
                    'simulator_setup'), 'dynamic_obstacles/person_two_legged.model.yaml')
                elif self.__ped_type==1:
                    # self.agent_topic_str+=f',{self.ns_prefix}pedsim_agent_{ped[0]}/dynamic_child'
                    self.__ped_file=os.path.join(rospkg.RosPack().get_path(
                    'simulator_setup'), 'dynamic_obstacles/person_two_legged_child.model.yaml')
                else:
                    # self.agent_topic_str+=f',{self.ns_prefix}pedsim_agent_{ped[0]}/dynamic_elder'
                    self.__ped_file=os.path.join(rospkg.RosPack().get_path(
                    'simulator_setup'), 'dynamic_obstacles/person_single_circle_elder.model.yaml')
            else:
                self.__ped_type=4
                # self.agent_topic_str+=f',{self.ns_prefix}pedsim_agent_{ped[0]}/dynamic_child'
                self.__ped_file=os.path.join(rospkg.RosPack().get_path(
                'simulator_setup'), 'obstacles/random.model.yaml')
            msg = Ped()
            msg.id = ped[0]
            msg.pos = Point()
            msg.pos.x = ped[1][0]
            msg.pos.y = ped[1][1]
            msg.pos.z = ped[1][2]
            msg.type = self.__ped_type
            msg.number_of_peds = 1
            msg.yaml_file = self.__ped_file
            msg.waypoints = []
            for pos in ped[2]:
                p = Point()
                p.x = pos[0]
                p.y = pos[1]
                p.z = pos[2]
                msg.waypoints.append(p)
            srv.peds.append(msg)
        max_num_try = 2
        i_curr_try = 0
        while i_curr_try < max_num_try:
            # try to call service
            response=self.__respawn_peds_srv.call(srv.peds)
            # response=self.__spawn_ped_srv.call(srv.peds)
            if not response.finished:  # if service not succeeds, do something and redo service
                rospy.logwarn(
                    f"spawn human failed! trying again... [{i_curr_try+1}/{max_num_try} tried]")
                # rospy.logwarn(response.message)
                i_curr_try += 1
            else:
                break
        self.__peds = peds
        # rospy.set_param(f'{self.ns_prefix}agent_topic_string', self.agent_topic_str)
        return

    def spawn_random_peds_in_world(self, n:int, safe_distance:float=2.5, forbidden_zones: Union[list, None] = None):
        """
        Spawning n random pedestrians in the whole world.
        :param n number of pedestrians that will be spawned.
        :param map the occupancy grid of the current map (TODO: the map should be updated every spawn)
        :safe_distance [meter] for sake of not exceeding the safety distance at the beginning phase
        """
        ped_array =np.array([],dtype=object).reshape(0,3)
        # self.human_id+=1
        # if  not self.circlePattern:
        #1 to 15
        for i in range(n):
            [x, y, theta] = get_random_pos_on_map(self._free_space_indices, self.map, safe_distance, forbidden_zones)
            waypoints = np.array( [x, y, 1]).reshape(1, 3) # the first waypoint
            # if random.uniform(0.0, 1.0) < 0.8:
            safe_distance = 0.1 # the other waypoints don't need to avoid robot
            for j in range(1000):
                dist = 0
                while dist < 8:
                    [x2, y2, theta2] = get_random_pos_on_map(self._free_space_indices, self.map, safe_distance, forbidden_zones)
                    dist = np.linalg.norm([waypoints[-1,0] - x2,waypoints[-1,1] - y2])
                    # if dist>=8: print(dist)
                waypoints = np.vstack([waypoints, [x2, y2, 1]])
            ped=np.array([i+1, [x, y, 0.0], waypoints],dtype=object)
            ped_array=np.vstack([ped_array,ped])

        self.__respawn_peds(ped_array)

    def __remove_all_peds(self):
        """
        Removes all pedestrians, that has been spawned so far
        """
        srv = SetBool()
        srv.data = True
        self.__remove_all_peds_srv.call(srv.data)
        self.__peds = []
        return

    def _generate_vertices_for_polygon_obstacle(self,num_vertices:int): 
        [x1, y1, theta1] = get_random_pos_on_map(self._free_space_indices, self.map, 0.3)        
        angle = 2*np.pi*np.random.random(1)
        radius = 1.5
        point = np.array([x1+np.cos(angle)*radius, y1+np.sin(angle)*radius])
        vertex=point.reshape(1,2)
        for i in range(num_vertices-1):
            dist = 0
            while dist <1.0 or dist > 1.3:
                angle = 2*np.pi*np.random.random(1)
                x2, y2= x1+np.cos(angle)*radius, y1+np.sin(angle)*radius
                dist = np.linalg.norm([vertex[-1,0] - x2,vertex[-1,1] - y2])
            vertex=np.vstack([vertex,[x2[0],y2[0]]])
        # print(num_border_vertex)
        # print('start_pos',[x1, y1, theta1])
        return vertex

    def _add_map_border_into_pedsim(self):
        border_vertex=generate_map_inner_border(self._free_space_indices,self.map)
        self.map_border_vertices=border_vertex
        add_pedsim_srv=SpawnObstacleRequest()
        size=border_vertex.shape[0]
        for i in range(size):
            lineObstacle=LineObstacle()
            lineObstacle.start.x,lineObstacle.start.y=border_vertex[i,0],border_vertex[i,1]
            lineObstacle.end.x,lineObstacle.end.y=border_vertex[(i+1)%size,0],border_vertex[(i+1)%size,1]
            add_pedsim_srv.staticObstacles.obstacles.append(lineObstacle)
        self.__add_obstacle_srv.call(add_pedsim_srv)

    def move_all_peds(self, episode:int):
        """
        Move all pedestrians to a new initial place at the beginning of every episode
        """
        srv = MovePedsRequest()
        srv.episode = episode
        waypoints = np.array([]).reshape(0, 2)
        if self.circlePattern:
            numCircleHuman=int(self.num_humans*self.mixRate)
            wp_srv=get_circluar_pattern_on_map(self._free_space_indices, self.map, numCircleHuman, gruppe_radius = 7.5, safe_dist=0.0)
            waypoints=wp_srv[:, :2]
            for wp in wp_srv:
                p=Point()
                p.x = wp[0]
                p.y = wp[1]
                p.z = wp[2]
                srv.pattern_waypoints.append(p)
        max_num_try = 2
        i_curr_try = 0
        while i_curr_try < max_num_try:
            # try to call service
            response=self.__move_peds_srv.call(srv)
            if not response.finished:  # if service not succeeds, do something and redo service
                rospy.logwarn(
                    f"move human failed! trying again... [{i_curr_try+1}/{max_num_try} tried]")
                # rospy.logwarn(response.message)
                i_curr_try += 1
            else:
                break
        # if  not self.circlePattern:
        for wp in response.waypoints:
            waypoints = np.vstack([waypoints, [wp.x, wp.y]])
        return waypoints
    ########################################################
    #Methods for maze########################################
    ########################################################
    def build_maze(self):
        self.maze=Maze()
        self.l_wall_shape, self.s_wall_shape, self._free_space_indices = self.maze.build_maze(self._free_space_indices, self.map)
        self.register_maze()
        # print('long wall centers2', self.l_walls)

    def register_maze(self):
        #4 short walls and 1 long wall
        self.register_walls_of_maze(4, 1, self.l_wall_shape, self.s_wall_shape)
    
    def update_maze(self):
        shortWallCenters, longWallCenters, theta_short_list, theta_long_list, shortWallVertices, longWallVertices= self.maze.update_maze()
        for i, wall_name in enumerate(self.s_wall_list):
            move_model_request = MoveModelRequest()
            move_model_request.name = wall_name
            move_model_request.pose.x = shortWallCenters[i,0]
            move_model_request.pose.y = shortWallCenters[i,1]
            move_model_request.pose.theta =theta_short_list[i]
            self._srv_move_model(move_model_request)
        # print('short', shortWallVertices)
        self._add_wall_into_pedsim(shortWallVertices)

        for i, wall_name in enumerate(self.l_wall_list):
            move_model_request = MoveModelRequest()
            move_model_request.name = wall_name
            move_model_request.pose.x = longWallCenters[i,0]
            move_model_request.pose.y = longWallCenters[i,1]
            move_model_request.pose.theta =theta_long_list[i]
            self._srv_move_model(move_model_request)
        # print('long', longWallVertices)
        self._add_wall_into_pedsim(longWallVertices)

    def register_walls_of_maze(self, num_short_walls: int, num_long_walls: int, vertices_long: np.ndarray, vertices_short: np.ndarray):
        """register walls with polygon shape.
        Args:
            num_walls (int): number of the obstacles.
        """
        shortWallCenters, longWallCenters, theta_short_list, theta_long_list, shortWallVertices, longWallVertices = self.maze.update_maze()
        for i in range(num_short_walls):
            model_path= self._generate_wall_yaml(vertices_short, True)
            # self.register_obstacles(1,model_path, start_pos, vertexArray, type_obstacle='static')
            max_num_try = 5
            i_curr_try = 0
            while i_curr_try < max_num_try:
                spawn_request = SpawnModelRequest()
                spawn_request.yaml_path = model_path
                spawn_request.name = f'shortwall_{i}'
                spawn_request.ns = rospy.get_namespace()
                spawn_request.pose.x = shortWallCenters[i,0]
                spawn_request.pose.y = shortWallCenters[i,1]
                spawn_request.pose.theta = theta_short_list[i]
                # try to call service
                response = self._srv_spawn_model.call(spawn_request)
                if not response.success:  # if service not succeeds, do something and redo service
                    rospy.logwarn(
                        f"({self.ns}) spawn object {spawn_request.name} failed! trying again... [{i_curr_try+1}/{max_num_try} tried]")
                    rospy.logwarn(response.message)
                    i_curr_try += 1
                else:
                    self.s_wall_list.append(spawn_request.name)
                    # #tell the info of polygon obstacles to pedsim
                    self._add_wall_into_pedsim(shortWallVertices)
                    break
            if i_curr_try == max_num_try:
                raise rospy.ServiceException(f"({self.ns}) failed to register walls")
            os.remove(model_path)

        for i in range(num_long_walls):
            model_path= self._generate_wall_yaml(vertices_long, False)
            # self.register_obstacles(1,model_path, start_pos, vertexArray, type_obstacle='static')
            max_num_try = 5
            i_curr_try = 0
            while i_curr_try < max_num_try:
                spawn_request = SpawnModelRequest()
                spawn_request.yaml_path = model_path
                spawn_request.name = f'longwall_{i}'
                spawn_request.ns = rospy.get_namespace()
                spawn_request.pose.x = longWallCenters[i,0]
                spawn_request.pose.y = longWallCenters[i,1]
                spawn_request.pose.theta = theta_long_list[i]
                # try to call service
                response = self._srv_spawn_model.call(spawn_request)
                if not response.success:  # if service not succeeds, do something and redo service
                    rospy.logwarn(
                        f"({self.ns}) spawn object {spawn_request.name} failed! trying again... [{i_curr_try+1}/{max_num_try} tried]")
                    rospy.logwarn(response.message)
                    i_curr_try += 1
                else:
                    self.l_wall_list.append(spawn_request.name)
                    #tell the info of polygon obstacles to pedsim
                    self._add_wall_into_pedsim(longWallVertices)
                    break
            if i_curr_try == max_num_try:
                raise rospy.ServiceException(f"({self.ns}) failed to register walls")
            os.remove(model_path)

    def _generate_wall_yaml(self, vertices, short: bool):
        # since flatland  can only config the model by parsing the yaml file, we need to create a file for every random obstacle
        wall_path = os.path.join(rospkg.RosPack().get_path(
            'simulator_setup'), 'walls')
        os.makedirs(wall_path, exist_ok=True)
        if short:
            wall_name = self.ns+"shortWall.model.yaml"
        else:
            wall_name = self.ns+"longWall.model.yaml"
        yaml_path = os.path.join(wall_path, wall_name)
        # define body
        body = {}
        body["name"] = "static_object"
        body["type"] = "static"
        body["color"] = [0.33, 0.34, 0.32, 0.75]
        body["footprints"] = []

        # define footprint
        f = {}
        f["density"] = 1
        f['restitution'] = 0
        f["layers"] = ["all"]
        f["collision"] = 'true'
        f["sensor"] = "false"
        f["type"] = "polygon"
        f["points"] = vertices.astype(np.float).tolist()

        body["footprints"].append(f)
        # define dict_file
        dict_file = {'bodies': [body]}
        with open(yaml_path, 'w') as fd:
            yaml.dump(dict_file, fd)
        return yaml_path

    def _add_wall_into_pedsim(self, vertices):
        add_pedsim_srv=SpawnObstacleRequest()
        # walls always have 4 vertices
        size= 4
        for v in vertices:
            for i in range(size):
                lineObstacle=LineObstacle()
                lineObstacle.start.x, lineObstacle.start.y= v[i,0],  v[i,1]
                lineObstacle.end.x, lineObstacle.end.y= v[(i+1)%size,0], v[(i+1)%size,1]
                add_pedsim_srv.staticObstacles.obstacles.append(lineObstacle)
        self.__add_obstacle_srv.call(add_pedsim_srv)
#############################
#####methods for static walls####
#############################
    def register_static_walls(self):
        vertices1= np.array([[6.7, 18.35], 
                                    [14.7, 18.35], 
                                    [14.7, 14.35], 
                                    [6.7, 14.35]])
        self.register_walls(vertices1, 1)
        vertices2= np.array([[6.7, -4.45], 
                                    [14.7, -4.45], 
                                    [14.7, -0.45], 
                                    [6.7, -0.45]])
        self.register_walls(vertices2, 2)
            

    def register_walls(self, vertices, i:int):
        model_path= self._generate_wall_yaml(vertices, True)
        max_num_try = 5
        i_curr_try = 0
        while i_curr_try < max_num_try:
            spawn_request = SpawnModelRequest()
            spawn_request.yaml_path = model_path
            spawn_request.name = f'staticwall_{i}'
            spawn_request.ns = rospy.get_namespace()
            spawn_request.pose.x = 0.0
            spawn_request.pose.y = 0.0
            spawn_request.pose.theta = 0.0
            # try to call service
            response = self._srv_spawn_model.call(spawn_request)
            if not response.success:  # if service not succeeds, do something and redo service
                rospy.logwarn(
                    f"({self.ns}) spawn object {spawn_request.name} failed! trying again... [{i_curr_try+1}/{max_num_try} tried]")
                rospy.logwarn(response.message)
                i_curr_try += 1
            else:
                # self.s_wall_list.append(spawn_request.name)
                # #tell the info of polygon obstacles to pedsim
                # self._add_wall_into_pedsim(shortWallVertices)
                break
        if i_curr_try == max_num_try:
            raise rospy.ServiceException(f"({self.ns}) failed to register walls")
        os.remove(model_path)

####################################
#####methods for static polygons####
####################################
    def __spawn_polygons(self, polygons):
        """
        Spawning zero velocity agents in the simulation. The type of pedestrian is randomly decided here.
        ZEROER=4
        :param  start_pos start position of the pedestrian.
        :param  wps waypoints the pedestrian is supposed to walk to.
        :param  id id of the pedestrian.
        """
        num_vertices_min = 3
        num_vertices_max = 5
        srv = SpawnZeroAgents()
        srv.polygons = []
        for i, po in enumerate(polygons):
            num_vertices = random.randint(num_vertices_min, num_vertices_max)
            vertices=self._generate_vertices_for_polygon_obstacle(num_vertices)
            polygon_file, _ =self._generate_static_obstacle_polygon_yaml(vertices, i)
            msg = ZeroAgent()
            msg.id = po[0]
            msg.pos = Point()
            msg.pos.x = po[1][0]
            msg.pos.y = po[1][1]
            msg.pos.z = po[1][2]
            msg.type = 4
            msg.number_of_agents = 1
            msg.yaml_file = polygon_file
            msg.waypoints = []
            for pos in po[2]:
                p = Point()
                p.x = pos[0]
                p.y = pos[1]
                p.z = pos[2]
                msg.waypoints.append(p)
            srv.polygons.append(msg)
        max_num_try = 2
        i_curr_try = 0
        while i_curr_try < max_num_try:
            # try to call service
            response=self.add_polygon_service_.call(srv.polygons)
            # response=self.__spawn_ped_srv.call(srv.peds)
            if not response.finished:  # if service not succeeds, do something and redo service
                rospy.logwarn(
                    f"spawn polygon failed! trying again... [{i_curr_try+1}/{max_num_try} tried]")
                # rospy.logwarn(response.message)
                i_curr_try += 1
            else:
                break
        return

    def spawn_random_polygons_in_world(self, n:int, safe_distance:float=2.5, forbidden_zones: Union[list, None] = None):
        """
        Spawning n random pedestrians in the whole world.
        :param n number of pedestrians that will be spawned.
        :param map the occupancy grid of the current map (TODO: the map should be updated every spawn)
        :safe_distance [meter] for sake of not exceeding the safety distance at the beginning phase
        """
        polygon_array =np.array([],dtype=object).reshape(0,3)
        for i in range(n):
            [x, y, theta] = get_random_pos_on_map(self._free_space_indices, self.map, safe_distance, forbidden_zones)
            waypoints = np.array( [x, y, 1]).reshape(1, 3) # the first waypoint
            safe_distance = 0.1 # the other waypoints don't need to avoid robot
            for j in range(1000):
                dist = 0
                while dist < 8:
                    [x2, y2, theta2] = get_random_pos_on_map(self._free_space_indices, self.map, safe_distance, forbidden_zones)
                    dist = np.linalg.norm([waypoints[-1,0] - x2,waypoints[-1,1] - y2])
                waypoints = np.vstack([waypoints, [x2, y2, 1]])
            polygon=np.array([i+1, [x, y, 0.0], waypoints],dtype=object)
            polygon_array=np.vstack([polygon_array,polygon])
        self.__spawn_polygons(polygon_array)

    def __remove_all_polygons(self):
        """
        Removes all pedestrians, that has been spawned so far
        """
        srv = SetBool()
        srv.data = True
        self.__remove_all_polygons_srv.call(srv.data)
        return

    def register_polygon(self, num_obstacles: int):
        """register dynamic obstacles human.

        Args:
            num_obstacles (int): number of the obstacles.        """
        model_path = os.path.join(rospkg.RosPack().get_path(
        'simulator_setup'), 'dynamic_obstacles/person_two_legged.model.yaml')
        self.num_polygons=num_obstacles