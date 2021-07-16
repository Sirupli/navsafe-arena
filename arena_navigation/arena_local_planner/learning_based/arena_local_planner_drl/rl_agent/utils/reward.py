import numpy as np
from numpy.lib.utils import safe_eval
import rospy
from geometry_msgs.msg import Pose2D
from typing import Tuple
import scipy.spatial
from rl_agent.utils.debug import timeit

class RewardCalculator():
    def __init__(self, 
                 robot_radius: float, 
                 safe_dist: float, 
                 goal_radius: float, 
                 rule: str = 'rule_00',
                 extended_eval: bool = False):
        """
        A class for calculating reward based various rules.


        :param safe_dist (float): The minimum distance to obstacles or wall that robot is in safe status.
                                  if the robot get too close to them it will be punished. Unit[ m ]
        :param goal_radius (float): The minimum distance to goal that goal position is considered to be reached. 
        """
        self.curr_reward = 0
        # additional info will be stored here and be returned alonge with reward.
        self.info = {}
        self.robot_radius = robot_radius
        self.goal_radius = goal_radius
        self.last_goal_dist = None
        self.last_dist_to_path = None
        self.last_action = None
        self.safe_dist = safe_dist
        #TODO: should be global setting
        self.safe_dist_adult= 1.0
        self.safe_dist_child= 1.2
        self.safe_dist_elder= 1.5
        self.safe_dist_talking= 0.8
        self._extended_eval = extended_eval

        self.kdtree = None
        
        self.last_goal_dist = None
        self.last_dist_to_path = None
        self.last_adult_min= None
        self.last_child_min= None
        self.last_elder_min= None
        self.cum_reward=0

        self._cal_funcs = {
            'rule_00': RewardCalculator._cal_reward_rule_00,
            'rule_01': RewardCalculator._cal_reward_rule_01,
            'rule_02': RewardCalculator._cal_reward_rule_02,
            'rule_03': RewardCalculator._cal_reward_rule_03,
            'rule_04': RewardCalculator._cal_reward_rule_04,
            'rule_05': RewardCalculator._cal_reward_rule_05,
            'rule_06': RewardCalculator._cal_reward_rule_06,
            'rule_07': RewardCalculator._cal_reward_rule_07,
            }
        self.cal_func = self._cal_funcs[rule]

    def reset(self):
        """
        reset variables related to the episode
        """
        self.last_goal_dist = None
        self.last_dist_to_path = None
        self.last_adult_min= None
        self.last_child_min= None
        self.last_elder_min= None
        self.ex_adult_steps= 0
        self.ex_child_steps= 0
        self.ex_elder_steps= 0
        self.cum_reward=0
        self.last_action = None
        self.kdtree = None

    def _reset(self):
        """
        reset variables related to current step
        """
        self.curr_reward = 0
        self.info = {}

    def get_history_info(self):
        return [self.last_adult_min, self.last_child_min, self.last_elder_min, self.ex_adult_steps, self.ex_child_steps, self.ex_elder_steps, self.cum_reward]
    
    def get_reward(self,
    laser_scan:np.ndarray,
    goal_in_robot_frame: Tuple[float,float],
    *args, **kwargs):
        """
        Args:
            laser_scan (np.ndarray): 
            goal_in_robot_frame (Tuple[float,float]: position (rho, theta) of the goal in robot frame (Polar coordinate) 
            adult_in_robot_frame(np.ndarray)
        """
        self._reset()
        self.cal_func(self, laser_scan, goal_in_robot_frame,*args,**kwargs)
        self.cum_reward+=self.curr_reward
        return self.curr_reward, self.info

    def _cal_reward_rule_00(self, 
                            laser_scan: np.ndarray, 
                            goal_in_robot_frame: Tuple[float,float],
                            *args,**kwargs):
        self._reward_goal_reached(
            goal_in_robot_frame)
        self._reward_safe_dist(
            laser_scan, punishment=0.25)
        self._reward_collision(
            laser_scan)
        self._reward_goal_approached(
            goal_in_robot_frame, reward_factor=0.3, penalty_factor=0.4)

    def _cal_reward_rule_01(self, 
                            laser_scan: np.ndarray, 
                            goal_in_robot_frame: Tuple[float,float],
                            *args,**kwargs):
        self._reward_distance_traveled(
            kwargs['action'], consumption_factor=0.0075)
        self._reward_goal_reached(
            goal_in_robot_frame, reward=15)
        self._reward_safe_dist(
            laser_scan, punishment=0.25)
        self._reward_collision(
            laser_scan, punishment=10)
        self._reward_goal_approached(
            goal_in_robot_frame, reward_factor=0.3, penalty_factor=0.4)

    def _cal_reward_rule_02(self, 
                            laser_scan: np.ndarray, 
                            goal_in_robot_frame: Tuple[float,float],
                            *args,**kwargs):
        self._reward_distance_traveled(
            kwargs['action'], consumption_factor=0.0075)
        self._reward_following_global_plan(
            kwargs['global_plan'], kwargs['robot_pose'])
        self._reward_goal_reached(
            goal_in_robot_frame, reward=15)
        self._reward_safe_dist(
            laser_scan, punishment=0.25)
        self._reward_collision(
            laser_scan, punishment=10)
        self._reward_goal_approached(
            goal_in_robot_frame, reward_factor=0.3, penalty_factor=0.4)

    def _cal_reward_rule_03(self, laser_scan: np.ndarray, goal_in_robot_frame: Tuple[float,float], *args,**kwargs):
        
        self._reward_goal_reached(goal_in_robot_frame, reward=2)
        self._reward_safe_dist(laser_scan)
        self._reward_collision(laser_scan, punishment=4)
        self._reward_goal_approached3(goal_in_robot_frame, kwargs['episode_time'])
        self._reward_adult_safety_dist3(kwargs['adult_in_robot_frame'], punishment=0.08) #0.05 0.07
        self._calculate_min_dist_adult(kwargs['adult_in_robot_frame'])
        self._reward_child_safety_dist3(kwargs['child_in_robot_frame'], punishment=0.08)
        self._calculate_min_dist_child(kwargs['child_in_robot_frame'])
        self._reward_elder_safety_dist3(kwargs['elder_in_robot_frame'], punishment=0.08)
        self._calculate_min_dist_elder(kwargs['elder_in_robot_frame'])

    # def _cal_reward_rule_04(self, 
    #                         laser_scan: np.ndarray, 
    #                         goal_in_robot_frame: Tuple[float,float],
    #                         *args,**kwargs):
    #     self._reward_following_global_plan(
    #         kwargs['global_plan'], kwargs['robot_pose'], kwargs['action'])
    #     if laser_scan.min() > self.safe_dist:
    #         self._reward_distance_global_plan(
    #             kwargs['global_plan'], kwargs['robot_pose'], reward_factor=0.2, penalty_factor=0.3)
    #     else:
    #         self.last_dist_to_path = None
    #     self._reward_goal_reached(
    #         goal_in_robot_frame, reward=15)
    #     self._reward_safe_dist(
    #         laser_scan, punishment=0.25)
    #     self._reward_collision(
    #         laser_scan, punishment=10)
    #     self._reward_goal_approached(
    #         goal_in_robot_frame, reward_factor=0.3, penalty_factor=0.4)

    def _cal_reward_rule_04(self, 
                             laser_scan: np.ndarray, 
                             goal_in_robot_frame: Tuple[float,float],
                             *args,**kwargs):
        self._reward_abrupt_direction_change(
            kwargs['action'])
        self._reward_following_global_plan(
            kwargs['global_plan'], kwargs['robot_pose'], kwargs['action'])
        if laser_scan.min() > self.safe_dist:
            self._reward_distance_global_plan(
                kwargs['global_plan'], kwargs['robot_pose'], reward_factor=0.2, penalty_factor=0.3)
        else:
            self.last_dist_to_path = None
        self._reward_goal_reached(
            goal_in_robot_frame, reward=15)
        self._reward_safe_dist(
            laser_scan, punishment=0.25)
        self._reward_collision(
            laser_scan, punishment=10)
        self._reward_goal_approached(
            goal_in_robot_frame, reward_factor=0.3, penalty_factor=0.4)

    def _cal_reward_rule_05(self, laser_scan: np.ndarray, goal_in_robot_frame: Tuple[float,float], adult_in_robot_frame:np.ndarray, 
                                                        child_in_robot_frame:np.ndarray, elder_in_robot_frame:np.ndarray,  current_time_step:float, *args,**kwargs):
        
        self._reward_goal_reached(goal_in_robot_frame, reward=2)
        self._reward_safe_dist(laser_scan)
        self._reward_collision(laser_scan, punishment=4)
        self._reward_goal_approached3(goal_in_robot_frame,current_time_step)
        self._reward_adult_safety_dist(adult_in_robot_frame, punishment=4) #0.05 0.07
        self._reward_child_safety_dist(child_in_robot_frame, punishment=4)
        self._reward_elder_safety_dist(elder_in_robot_frame, punishment=4)

    def _cal_reward_rule_06(self, laser_scan: np.ndarray, goal_in_robot_frame: Tuple[float,float], adult_in_robot_frame:np.ndarray, 
                                                        child_in_robot_frame:np.ndarray, elder_in_robot_frame:np.ndarray,  current_time_step:float, *args,**kwargs):
        
        self._reward_goal_reached(goal_in_robot_frame, reward=2)
        self._reward_safe_dist(laser_scan)
        self._reward_collision(laser_scan, punishment=4)
        self._reward_goal_approached3(goal_in_robot_frame,current_time_step)
        self._reward_adult_safety_dist1(adult_in_robot_frame, punishment=4) #0.05 0.07
        self._reward_child_safety_dist1(child_in_robot_frame, punishment=4)
        self._reward_elder_safety_dist1(elder_in_robot_frame, punishment=4)

    def _cal_reward_rule_07(self, laser_scan: np.ndarray, goal_in_robot_frame: Tuple[float,float], *args,**kwargs):
        
        self._reward_goal_reached(goal_in_robot_frame, reward=2)
        self._reward_safe_dist(laser_scan)
        self._reward_collision(laser_scan, punishment=4)
        self._reward_goal_approached3(goal_in_robot_frame,kwargs['episode_time'])
        self._reward_human_safety_dist_adult(kwargs['isInDangerZoneAdult'],kwargs['RF_and_DcAdult'], punishment=0.15) #0.05 0.07
        self._reward_human_safety_dist_child(kwargs['isInDangerZoneChild'],kwargs['RF_and_DcChild'], punishment=0.15) #0.05 0.07
        self._reward_human_safety_dist_elder(kwargs['isInDangerZoneElder'],kwargs['RF_and_DcElder'], punishment=0.15) #0.05 0.07
        self._calculate_min_dist_adult(kwargs['adult_in_robot_frame'])
        self._calculate_min_dist_child(kwargs['child_in_robot_frame'])
        self._calculate_min_dist_elder(kwargs['elder_in_robot_frame'])

        
    def _reward_goal_reached(self,
                             goal_in_robot_frame = Tuple[float,float], 
                             reward: float=15):
        """
        Reward for reaching the goal.
        
        :param goal_in_robot_frame (Tuple[float,float]): position (rho, theta) of the goal in robot frame (Polar coordinate) 
        :param reward (float, optional): reward amount for reaching. defaults to 15
        """
        if goal_in_robot_frame[0] < self.goal_radius*2:
            self.curr_reward = reward
            self.info['is_done'] = True
            self.info['done_reason'] = 2
            self.info['is_success'] = 1
        else:
            self.info['is_done'] = False

    def _reward_goal_approached(self, 
                                goal_in_robot_frame = Tuple[float,float],
                                reward_factor: float=0.3,
                                penalty_factor: float=0.5):
        """
        Reward for approaching the goal.
        
        :param goal_in_robot_frame (Tuple[float,float]): position (rho, theta) of the goal in robot frame (Polar coordinate)
        :param reward_factor (float, optional): positive factor for approaching goal. defaults to 0.3
        :param penalty_factor (float, optional): negative factor for withdrawing from goal. defaults to 0.5
        """
        if self.last_goal_dist is not None:
            #goal_in_robot_frame : [rho, theta]
            
            # higher negative weight when moving away from goal 
            # (to avoid driving unnecessary circles when train in contin. action space)
            if (self.last_goal_dist - goal_in_robot_frame[0]) > 0:
                w = reward_factor
            else:
                w = penalty_factor
            reward = w*(self.last_goal_dist - goal_in_robot_frame[0])

            # print("reward_goal_approached:  {}".format(reward))
            self.curr_reward += reward
        self.last_goal_dist = goal_in_robot_frame[0]

    def _reward_adult_safety_dist(self, adult_in_robot_frame, punishment = 80):
        if adult_in_robot_frame.shape[0] != 0:
            min_adult_dist=adult_in_robot_frame[:,0].min()
            # min_adult_dist_0=adult_in_robot_frame[0,0]
            # print(min_adult_dist==min_adult_dist_0)
            if self.last_adult_min is None:
                self.last_adult_min=min_adult_dist
            else:
                if self.last_adult_min>min_adult_dist:
                    self.last_adult_min=min_adult_dist
            if min_adult_dist<self.safe_dist_adult:
                self.curr_reward -= punishment
                self.info['is_done'] = True
                self.info['done_reason'] = 3 #hit adult
                self.info['is_success'] = 0

    def _reward_child_safety_dist(self, child_in_robot_frame, punishment = 90):
        if child_in_robot_frame.shape[0]!=0:
            min_child_dist=child_in_robot_frame[:,0].min()
            if self.last_child_min is None:
                self.last_child_min=min_child_dist
            else:
                if self.last_child_min>min_child_dist:
                    self.last_child_min=min_child_dist
            if min_child_dist<self.safe_dist_child:
                self.curr_reward -= punishment
                self.info['is_done'] = True
                self.info['done_reason'] = 4 #hit child
                self.info['is_success'] = 0

    def _reward_elder_safety_dist(self, elder_in_robot_frame, punishment = 100):
        if elder_in_robot_frame.shape[0]!=0:
            min_elder_dist=elder_in_robot_frame[:,0].min()
            if self.last_elder_min is None:
                self.last_elder_min=min_elder_dist
            else:
                if self.last_elder_min>min_elder_dist:
                    self.last_elder_min=min_elder_dist
            if min_elder_dist<self.safe_dist_elder:
                self.curr_reward -= punishment
                self.info['is_done'] = True
                self.info['done_reason'] = 5 # hit elder
                self.info['is_success'] = 0

    def _reward_adult_safety_dist1(self, adult_in_robot_frame, punishment = 80):
        if adult_in_robot_frame.shape[0] != 0:
            min_adult_dist=adult_in_robot_frame[0,0]
            min_adult_behavior=adult_in_robot_frame[0,1]
            if self.last_adult_min is None:
                self.last_adult_min=min_adult_dist
            else:
                if self.last_adult_min>min_adult_dist:
                    self.last_adult_min=min_adult_dist
            if min_adult_dist<self.safe_dist_adult and min_adult_behavior !='talking':
                self.curr_reward -= punishment
                self.info['is_done'] = True
                self.info['done_reason'] = 3 #hit adult
                self.info['is_success'] = 0
            if min_adult_dist<self.safe_dist_talking and min_adult_behavior =='talking':
                self.curr_reward -= punishment
                self.info['is_done'] = True
                self.info['done_reason'] = 3 #hit adult
                self.info['is_success'] = 0

    def _reward_child_safety_dist1(self, child_in_robot_frame, punishment = 90):
        if child_in_robot_frame.shape[0]!=0:
            min_child_dist=child_in_robot_frame[0,0]
            min_child_behavior=child_in_robot_frame[0,1]
            if self.last_child_min is None:
                self.last_child_min=min_child_dist
            else:
                if self.last_child_min>min_child_dist:
                    self.last_child_min=min_child_dist
            if min_child_dist<self.safe_dist_child and min_child_behavior != 'talking' :
                self.curr_reward -= punishment
                self.info['is_done'] = True
                self.info['done_reason'] = 4 #hit child
                self.info['is_success'] = 0
            if min_child_dist<self.safe_dist_talking and min_child_behavior == 'talking' :
                self.curr_reward -= punishment
                self.info['is_done'] = True
                self.info['done_reason'] = 4 #hit child
                self.info['is_success'] = 0

    def _reward_elder_safety_dist1(self, elder_in_robot_frame, punishment = 100):
        if elder_in_robot_frame.shape[0]!=0:
            min_elder_dist=elder_in_robot_frame[0,0]
            min_elder_behavior=elder_in_robot_frame[0,1]
            if self.last_elder_min is None:
                self.last_elder_min=min_elder_dist
            else:
                if self.last_elder_min>min_elder_dist:
                    self.last_elder_min=min_elder_dist
            if min_elder_dist<self.safe_dist_elder and min_elder_behavior !='talking':
                self.curr_reward -= punishment
                self.info['is_done'] = True
                self.info['done_reason'] = 5 # hit elder
                self.info['is_success'] = 0
            if min_elder_dist<self.safe_dist_talking and min_elder_behavior == 'talking':
                self.curr_reward -= punishment
                self.info['is_done'] = True
                self.info['done_reason'] = 5 # hit elder
                self.info['is_success'] = 0

    def _reward_goal_approached3(self, goal_in_robot_frame, current_time_step):
        if self.last_goal_dist is not None:
            # higher negative weight when moving away from goal (to avoid driving unnecessary circles when train in contin. action space)
            if (self.last_goal_dist - goal_in_robot_frame[0]) > 0:
                w = 0.018*np.exp(1-current_time_step)
            elif (self.last_goal_dist - goal_in_robot_frame[0]) < 0:
                w = -0.05*np.exp(1)
            else:
                w = -0.03
            reward = round(w, 5)
            # print("reward_goal_approached:  {}".format(reward))
            self.curr_reward += reward
        self.last_goal_dist = goal_in_robot_frame[0]

    def _reward_human_safety_dist_adult(self, isInDangerZoneAdult,RF_and_DcAdult, punishment = 80):
        for i, danger in enumerate(isInDangerZoneAdult):
            if danger:
                self.curr_reward -= (RF_and_DcAdult[i,1]*(-punishment)/RF_and_DcAdult[i,0]+punishment)
                self.ex_adult_steps+= 1

    def _reward_human_safety_dist_child(self, isInDangerZoneChild,RF_and_DcChild, punishment = 80):
        for i, danger in enumerate(isInDangerZoneChild):
            if danger:
                self.curr_reward -= (RF_and_DcChild[i,1]*(-punishment)/RF_and_DcChild[i,0]+punishment)
                self.ex_child_steps+= 1

    def _reward_human_safety_dist_elder(self, isInDangerZoneElder,RF_and_DcElder, punishment = 80):
        for i, danger in enumerate(isInDangerZoneElder):
            if danger:
                self.curr_reward -= (RF_and_DcElder[i,1]*(-punishment)/RF_and_DcElder[i,0]+punishment)
                self.ex_elder_steps+= 1


    def _reward_adult_safety_dist3(self, adult_in_robot_frame, punishment = 80):
        if adult_in_robot_frame.shape[0]!=0:
            sd=0
            for dist_behavior in adult_in_robot_frame:
                if dist_behavior[1]=='talking':
                    sd=self.safe_dist_talking
                else:
                    sd=self.safe_dist_adult
                if dist_behavior[0]<sd:
                    self.ex_adult_steps+=1
                    self.curr_reward -= punishment*np.exp(1-dist_behavior[0]/sd)


    def _reward_child_safety_dist3(self, child_in_robot_frame, punishment = 90):
        if child_in_robot_frame.shape[0]!=0:
            sd=0
            for dist_behavior in child_in_robot_frame:
                if dist_behavior[1]=='talking':
                    sd=self.safe_dist_talking
                else:
                    sd=self.safe_dist_child
                if dist_behavior[0]<sd:
                    self.ex_child_steps+=1
                    self.curr_reward -= punishment*np.exp(1-dist_behavior[0]/sd)


    def _reward_elder_safety_dist3(self, elder_in_robot_frame, punishment = 100):
        if elder_in_robot_frame.shape[0]!=0:
            sd=0
            for dist_behavior in elder_in_robot_frame:
                if dist_behavior[1]=='talking':
                    sd=self.safe_dist_talking
                else:
                    sd=self.safe_dist_elder
                if dist_behavior[0]<sd:
                    self.ex_elder_steps+=1
                    self.curr_reward -= punishment*np.exp(1-dist_behavior[0]/sd)

    def _reward_collision(self,
                          laser_scan: np.ndarray, 
                          punishment: float=10):
        """
        Reward for colliding with an obstacle.
        
        :param laser_scan (np.ndarray): laser scan data
        :param punishment (float, optional): punishment for collision. defaults to 10
        """
        if laser_scan.min() <= self.robot_radius:
            self.curr_reward -= punishment
            
            if not self._extended_eval:
                self.info['is_done'] = True
                self.info['done_reason'] = 1
                self.info['is_success'] = 0
            else:
                self.info['crash'] = True

    def _reward_safe_dist(self, 
                          laser_scan: np.ndarray, 
                          punishment: float=0.15):
        """
        Reward for undercutting safe distance.
        
        :param laser_scan (np.ndarray): laser scan data
        :param punishment (float, optional): punishment for undercutting. defaults to 0.15
        """
        if laser_scan.min() < self.safe_dist:
            self.curr_reward -= punishment
            
            if self._extended_eval:
                self.info['safe_dist'] = True

    def _reward_not_moving(self, 
                           action: np.ndarray=None, 
                           punishment: float=0.01):
        """
        Reward for not moving. Only applies half of the punishment amount
        when angular velocity is larger than zero.
        
        :param action (np.ndarray (,2)): [0] - linear velocity, [1] - angular velocity 
        :param punishment (float, optional): punishment for not moving. defaults to 0.01
        """
        if action is not None and action[0] == 0.0:
            if action[1] == 0.0:
                self.curr_reward -= punishment
            else:
                self.curr_reward -= punishment/2

    def _reward_distance_traveled(self, 
                                  action: np.array = None, 
                                  punishment: float=0.01,
                                  consumption_factor: float=0.005):
        """
        Reward for driving a certain distance. Supposed to represent "fuel consumption".
        
        :param action (np.ndarray (,2)): [0] - linear velocity, [1] - angular velocity 
        :param punishment (float, optional): punishment when action can't be retrieved. defaults to 0.01
        :param consumption_factor (float, optional): weighted velocity punishment. defaults to 0.01
        """
        if action is None:
            self.curr_reward -= punishment
        else:
            lin_vel = action[0]
            ang_vel = action[1]
            reward = (lin_vel + (ang_vel*0.001)) * consumption_factor
        self.curr_reward -= reward
        
    def _reward_distance_global_plan(self, 
                                     global_plan: np.array, 
                                     robot_pose: Pose2D,
                                     reward_factor: float=0.1, 
                                     penalty_factor: float=0.15):
        """
        Reward for approaching/veering away the global plan. (Weighted difference between
        prior distance to global plan and current distance to global plan)
        
        :param global_plan: (np.ndarray): vector containing poses on global plan
        :param robot_pose (Pose2D): robot position
        :param reward_factor (float, optional): positive factor when approaching global plan. defaults to 0.1
        :param penalty_factor (float, optional): negative factor when veering away from global plan. defaults to 0.15
        """
        if global_plan is not None and len(global_plan) != 0:
            curr_dist_to_path, idx = self.get_min_dist2global_kdtree(
                global_plan, robot_pose)
            
            if self.last_dist_to_path is not None:
                if curr_dist_to_path < self.last_dist_to_path:
                    w = reward_factor
                else:
                    w = penalty_factor

                self.curr_reward += w * (self.last_dist_to_path - curr_dist_to_path)
            self.last_dist_to_path = curr_dist_to_path

    def _reward_following_global_plan(self, 
                                      global_plan: np.array, 
                                      robot_pose: Pose2D,
                                      action: np.array = None,
                                      dist_to_path: float=0.5):
        """
        Reward for travelling on the global plan. 
        
        :param global_plan: (np.ndarray): vector containing poses on global plan
        :param robot_pose (Pose2D): robot position
        :param action (np.ndarray (,2)): [0] = linear velocity, [1] = angular velocity 
        :param dist_to_path (float, optional): applies reward within this distance
        """
        if global_plan is not None and len(global_plan) != 0 and action is not None:
            curr_dist_to_path, idx = self.get_min_dist2global_kdtree(
                global_plan, robot_pose)
            
            if curr_dist_to_path <= dist_to_path:
                self.curr_reward += 0.1 * action[0]

    def get_min_dist2global_kdtree(self, 
                                   global_plan: np.array, 
                                   robot_pose: Pose2D):
        """
        Calculates minimal distance to global plan using kd tree search. 
        
        :param global_plan: (np.ndarray): vector containing poses on global plan
        :param robot_pose (Pose2D): robot position
        """
        if self.kdtree is None:      
            self.kdtree = scipy.spatial.cKDTree(global_plan)
        
        dist, index = self.kdtree.query([robot_pose.x, robot_pose.y])
        return dist, index

    def _reward_abrupt_direction_change(self,
                                        action: np.array=None):
        """
        Applies a penalty when an abrupt change of direction occured.
        
        :param action: (np.ndarray (,2)): [0] = linear velocity, [1] = angular velocity 
        """
        if self.last_action is not None:
            curr_ang_vel = action[1]
            last_ang_vel = self.last_action[1]

            vel_diff = abs(curr_ang_vel-last_ang_vel)
            self.curr_reward -= (vel_diff**4)/2500
        self.last_action = action

    def _calculate_min_dist_adult(self, adult_dists):
        if adult_dists.shape[0]!=0:
            min_adult_dist = adult_dists[:,0].min()
            if self.last_adult_min is None:
                self.last_adult_min = min_adult_dist
            else:
                if self.last_adult_min > min_adult_dist:
                    self.last_adult_min = min_adult_dist

    def _calculate_min_dist_child(self, child_dists):
        if child_dists.shape[0]!=0:
            min_child_dist = child_dists[:,0].min()
            if self.last_child_min is None:
                self.last_child_min=min_child_dist
            else:
                if self.last_child_min>min_child_dist:
                    self.last_child_min=min_child_dist

    def _calculate_min_dist_elder(self, elder_dists):
        if elder_dists.shape[0]!=0:
            min_elder_dist = elder_dists[:,0].min()
            if self.last_elder_min is None:
                self.last_elder_min=min_elder_dist
            else:
                if self.last_elder_min>min_elder_dist:
                    self.last_elder_min=min_elder_dist

