import numpy as np

from pydrake.all import (
    JacobianWrtVariable, Integrator, LeafSystem, BasicVector, 
    ConstantVectorSource, RigidTransform, SpatialVelocity, RollPitchYaw
)

CENTERPOINT = 0.88

class InverseKinematics(LeafSystem):
    def __init__(self, plant):
        LeafSystem.__init__(self)
        self.plant = plant
        self.plant_context = plant.CreateDefaultContext()
        self.iiwa = plant.GetModelInstanceByName("iiwa7")
        self.P = plant.GetBodyByName("base_link").body_frame()
        self.W = plant.world_frame()

        self.DeclareVectorInputPort("paddle_desired_velocity", BasicVector(3))
        self.DeclareVectorInputPort("paddle_desired_angular_velocity", BasicVector(3))
        self.DeclareVectorInputPort("iiwa_pos_measured", BasicVector(7))
        self.DeclareVectorOutputPort("iiwa_velocity", BasicVector(7), self.CalcOutput)
        self.iiwa_start = plant.GetJointByName("iiwa_joint_1").velocity_start()
        self.iiwa_end = plant.GetJointByName("iiwa_joint_7").velocity_start()

    def CalcOutput(self, context, output):
        q = self.GetInputPort("iiwa_pos_measured").Eval(context)
        v_P_desired = self.GetInputPort("paddle_desired_velocity").Eval(context)
        w_P_desired = self.GetInputPort("paddle_desired_angular_velocity").Eval(context)
        # pos_P_desired = self.GetInputPort("paddle_desired_velocity").Eval(context)
        self.plant.SetPositions(self.plant_context, self.iiwa, q)
        J_P = self.plant.CalcJacobianSpatialVelocity(
            self.plant_context, JacobianWrtVariable.kV, 
            self.P, [0,0,0], self.W, self.W)
        J_P = J_P[:,self.iiwa_start:self.iiwa_end+1]
        # print(w_P_desired)
        v = np.linalg.pinv(J_P).dot(np.hstack([w_P_desired, v_P_desired]))
        # v = np.linalg.pinv(J_P).dot(np.hstack([[0, 0, 0], v_P_desired]))
        # v = np.linalg.pinv(J_P).dot(np.hstack([[0, np.pi, 0], [0, 0, 0]]))

        # #overwrite for debugging
        # v = [0,0,0,0,0,0,0]
        output.SetFromVector(v)


class VelocityMirror(LeafSystem):
    def __init__(self, plant):
        LeafSystem.__init__(self)
        self.plant = plant
        self.plant_context = plant.CreateDefaultContext()
        self.iiwa = plant.GetModelInstanceByName("iiwa7")
        self.Paddle = plant.GetBodyByName("base_link")
        self.W = plant.world_frame()

        self.DeclareVectorInputPort("iiwa_pos_measured", BasicVector(7))
        self.DeclareVectorInputPort("iiwa_velocity_estimated", BasicVector(7))
        self.DeclareVectorInputPort("ball_pose", BasicVector(3))
        self.DeclareVectorInputPort("ball_velocity", BasicVector(3))
        self.DeclareVectorOutputPort("mirror_velocity", BasicVector(3), self.CalcOutput)

    def CalcOutput(self, context, output):
        q = self.GetInputPort("iiwa_pos_measured").Eval(context)
        v = self.GetInputPort("iiwa_velocity_estimated").Eval(context)
        p_Ball = np.array(self.GetInputPort("ball_pose").Eval(context))
        p_Ball_xy, p_Ball_z = p_Ball[:2], np.array([p_Ball[2]])
        v_Ball = np.array(self.GetInputPort("ball_velocity").Eval(context))
        v_Ball_xy, v_Ball_z = v_Ball[:2], np.array([v_Ball[2]])

        # Prevent from going crazy when lost ball
        if np.linalg.norm(p_Ball) > 5:
            output.SetFromVector([0, 0, 0])
            return


        # if v_Ball_z >= 0:
        #     v_Ball_z *= -1
        # else:
        #     v_Ball_z *= -1

        # This might help prevent it from hitting the ball harder and harder
        # by tapering off the mirror velocity as the ball gets faster, but maintaining
        # approx linear match at lower speeds
        if v_Ball_z >= 0:
            v_Ball_z = np.array([max(-1.8*np.log(v_Ball_z[0]+1), -2)]) # in m/s
        else:
            v_Ball_z = np.array([min(1.8*np.log(-v_Ball_z[0]+1), 2)]) # in m/s

        self.plant.SetPositionsAndVelocities(self.plant_context, self.iiwa, np.hstack([q, v]))
        p_Paddle_xy = np.array(self.plant.EvalBodyPoseInWorld(self.plant_context, self.Paddle).translation())[:2]
        v_Paddle_xy = np.array(self.plant.EvalBodySpatialVelocityInWorld(self.plant_context, self.Paddle).translational())[:2]
        
        K_p = 5
        K_d = 1
        v_P_desired = K_p*(p_Ball_xy - p_Paddle_xy) + K_d*(v_Ball_xy - v_Paddle_xy)
        v_P_desired = np.concatenate((v_P_desired, v_Ball_z))
        
        # Tune down with radial shape (1-x^2 - y^2)
        scale = (1 - (p_Ball_xy[0] - CENTERPOINT)**2 - p_Ball_xy[1]**2)

        # IDEA: Scale this further with the height of the ball?
        v_P_desired[2] *= scale
        output.SetFromVector(v_P_desired)


class AngularVelocityTilt(LeafSystem):
    def __init__(self, plant):
        LeafSystem.__init__(self)
        self.plant = plant
        self.plant_context = plant.CreateDefaultContext()
        self.iiwa = plant.GetModelInstanceByName("iiwa7")
        self.Ball = plant.GetBodyByName("ball")
        self.P = plant.GetBodyByName("base_link")
        self.W = plant.world_frame()

        self.DeclareVectorInputPort("iiwa_pos_measured", BasicVector(7))
        self.DeclareVectorInputPort("ball_pose", BasicVector(3))
        self.DeclareVectorInputPort("ball_velocity", BasicVector(3))
        self.DeclareVectorOutputPort("angular_velocity", BasicVector(3), self.CalcOutput)
        self.iiwa_start = plant.GetJointByName("iiwa_joint_1").velocity_start()
        self.iiwa_end = plant.GetJointByName("iiwa_joint_7").velocity_start()

    def CalcOutput(self, context, output):
        q = self.GetInputPort("iiwa_pos_measured").Eval(context)
        self.plant.SetPositions(self.plant_context, self.iiwa, q)
        p_Ball = self.GetInputPort("ball_pose").Eval(context)
        p_Ball_xy = p_Ball[:2]
        v_Ball_xy = np.array(self.GetInputPort("ball_velocity").Eval(context))[:2]
        R_Paddle = RollPitchYaw(self.plant.EvalBodyPoseInWorld(self.plant_context, self.P).rotation()).vector()
        roll_current, pitch_current, yaw_current = R_Paddle[0], R_Paddle[1], R_Paddle[2]

        # Prevent from going crazy when lost ball
        # print(np.linalg.norm(p_Ball))
        if np.linalg.norm(p_Ball) > 5:
            output.SetFromVector([0, 0, 0])
            return
        
        k = np.array([.5, .5])
        # roll_des = np.sign(B_y) * np.arctan(k * (1 - np.cos(B_y)))
        # pitch_des = np.sign(B_x - centerpoint) * np.arctan(k * (1 - np.cos(B_x - centerpoint_x)))
        sign_multiplier = np.array([1, 1])
        if np.sign(p_Ball_xy[0] - CENTERPOINT) != np.sign(v_Ball_xy[0]):
            # sign_multiplier[0] = .05
            sign_multiplier[0] = 0.5 / (10 * abs(v_Ball_xy[0]) + 1)
        if np.sign(p_Ball_xy[1]) != np.sign(v_Ball_xy[1]):
            # sign_multiplier[1] = .05
            sign_multiplier[1] = 0.5 / (10 * abs(v_Ball_xy[1]) + 1)
        
        # deltas = sign_multiplier * np.sign(p_Ball_xy[:2] - CENTERPOINT) * np.arctan(k * (1 - np.cos(p_Ball_xy[:2] - CENTERPOINT)))
        if p_Ball_xy[0] - CENTERPOINT == 0:
            pitch_des = 0
        else:
            pitch_des = -sign_multiplier[0] * np.arctan(k[0] * (1 - np.cos(p_Ball_xy[0] - CENTERPOINT))/(p_Ball_xy[0] - CENTERPOINT))
        
        if p_Ball[1] == 0:
            roll_des = 0
        else:
            roll_des = sign_multiplier[1] * np.arctan(k[1] * (1 - np.cos(p_Ball_xy[1]))/(p_Ball_xy[1]))
        yaw_des = 0

        # print(f"Ball: [{B_x}, {B_y}, {X_B[2]}]\nRPY current: [{roll_current}, {pitch_current}, {yaw_current}]\nRPY desired: [{roll_des}, {pitch_des}, {yaw_des}]")
            
        K_p = 5

        dw = K_p*np.array([roll_des-roll_current, pitch_des-pitch_current, yaw_des - yaw_current])
        # print(f"Commanded: {dw}\n")
        # dw = np.zeros_like(dw)
        output.SetFromVector(dw)
