"""Path execution utilities."""

import time
from threading import Thread
from typing import Any

import numpy as np
import shapely

from .a_star import AStarPlanner
from .prm import PRMPlanner
from .types import PathExecutor
from ..planning.actions import ExecutionResult, ExecutionStatus
from ..sensors.lidar import Lidar2D
from ..utils.logging import get_global_logger
from ..utils.path import Path
from ..utils.trajectory import get_constant_speed_trajectory, interpolate_trajectory


class ConstantVelocityExecutor(PathExecutor):
    """
    Executes a path with a linear trajectory assuming constant
    linear and angular velocity, and that the robot can perfectly
    go to the next pose.
    """

    def __init__(
        self,
        dt: float = 0.1,
        linear_velocity: float = 1.0,
        max_angular_velocity: float | None = None,
        validate_during_execution: bool = False,
        validation_dt: float = 0.5,
        validation_step_dist: float = 0.025,
        lidar_sensor_name: str | None = None,
        lidar_sensor_measurement_dt: float = 0.25,
    ) -> None:
        """
        Creates a constant velocity path executor.

        :param dt: Time step for creating a trajectory, in seconds.
        :param linear_velocity: Linear velocity, in m/s.
        :param max_angular_velocity: Maximum angular velocity, in rad/s.
        :param validate_during_execution: If True, runs a separate thread that validates the remaining path at a regular rate.
        :param validation_dt: Time step for validating the remaining path, in seconds.
        :param validation_step_dist: The step size for discretizing a straight line to check collisions.
        :param lidar_sensor_name: Name of the lidar sensor to use for detecting closed hallways.
        :param lidar_sensor_measurement_dt: Time step for taking lidar sensor measurement, in seconds.
        """
        super().__init__()
        self.dt = dt
        self.linear_velocity = linear_velocity
        self.max_angular_velocity = max_angular_velocity

        self.validation_timer: Thread | None = None
        self.validate_during_execution = validate_during_execution
        self.validation_dt = validation_dt
        self.validation_step_dist = validation_step_dist

        self.lidar_sensor_timer: Thread | None = None
        self.lidar_sensor_name = lidar_sensor_name
        self.lidar_sensor_measurement_dt = lidar_sensor_measurement_dt

        # Execution state
        self.reset_state()

    def reset_state(self) -> None:
        """
        Resets all the states for tracking the status of path execution.
        """
        self.current_traj_time = 0.0
        self.following_path = False  # Flag to track path following
        self.abort_execution = False  # Flag to abort internally
        self.cancel_execution = False  # Flag to cancel from user
        self.hallway_states_updated = False  # Flag to track hallway states updates

    def execute(
        self, path: Path, realtime_factor: float = 1.0, battery_usage: float = 0.0
    ) -> ExecutionResult:
        """
        Generates and executes a trajectory on the robot.

        :param path: Path to execute on the robot.
        :param realtime_factor: A multiplier on the execution time relative to
            real time, defaults to 1.0.
        :param battery_usage: Robot battery usage per unit distance.
        :return: An object describing the execution result.
        """
        if self.robot is None:
            message = "No robot attached to execute the trajectory."
            get_global_logger().warning(message)
            return ExecutionResult(
                status=ExecutionStatus.PRECONDITION_FAILURE,
                message=message,
            )
        elif path.num_poses < 2:
            message = "Not enough waypoints in path to execute."
            self.robot.logger.warning(message)
            return ExecutionResult(
                status=ExecutionStatus.PRECONDITION_FAILURE,
                message=message,
            )

        # Convert the path to an interpolated trajectory.
        self.traj = get_constant_speed_trajectory(
            path,
            linear_velocity=self.linear_velocity,
            max_angular_velocity=self.max_angular_velocity,
        )
        if self.traj is None:
            message = "Failed to get trajectory from path."
            self.robot.logger.warning(message)
            return ExecutionResult(
                status=ExecutionStatus.PRECONDITION_FAILURE,
                message=message,
            )

        traj_interp = interpolate_trajectory(self.traj, self.dt)
        if traj_interp is None:
            message = "Failed to interpolate trajectory."
            self.robot.logger.warning(message)
            return ExecutionResult(
                status=ExecutionStatus.PRECONDITION_FAILURE,
                message=message,
            )

        self.reset_state()
        self.following_path = True

        # Optionally, kick off the path validation timer.
        if self.validate_during_execution and self.robot.world is not None:
            self.validation_timer = Thread(target=self.validate_remaining_path)
            self.validation_timer.start()

        if self.robot.fog_hallways:
            self.lidar_sensor_timer = Thread(target=self.detect_closed_hallway)
            self.lidar_sensor_timer.start()

        # Execute the trajectory.
        status = ExecutionStatus.SUCCESS
        message = ""
        sleep_time = self.dt / realtime_factor
        prev_pose = traj_interp.poses[0]
        for i in range(traj_interp.num_points()):
            start_time = time.time()
            cur_pose = traj_interp.poses[i]
            self.current_traj_time = traj_interp.t_pts[i]
            self.robot.set_pose(cur_pose)
            if self.robot.manipulated_object is not None:
                self.robot.manipulated_object.set_pose(cur_pose)

            if self.abort_execution:
                if self.validate_during_execution and (
                    self.validation_timer is not None
                ):
                    self.validation_timer.join()
                if self.robot.fog_hallways and (self.lidar_sensor_timer is not None):
                    self.lidar_sensor_timer.join()
                message = "Trajectory execution aborted."
                self.robot.logger.info(message)
                status = ExecutionStatus.EXECUTION_FAILURE
                break
            if self.cancel_execution:
                self.cancel_execution = False
                message = "Trajectory execution canceled by user."
                self.robot.logger.info(message)
                status = ExecutionStatus.CANCELED
                break

            # Simulate battery usage and exit if the battery is fully depleted.
            self.robot.battery_level -= battery_usage * cur_pose.get_linear_distance(
                prev_pose
            )
            if self.robot.battery_level <= 0.0:
                self.robot.battery_level = 0.0
                message = "Battery depleted while navigating."
                self.robot.logger.warning(message)
                status = ExecutionStatus.EXECUTION_FAILURE
                break

            prev_pose = cur_pose
            time.sleep(max(0, sleep_time - (time.time() - start_time)))

        # check if path planner needs to be reset at the end of path execution
        if self.hallway_states_updated:
            if isinstance(self.robot.path_planner, PRMPlanner) or isinstance(
                self.robot.path_planner, AStarPlanner
            ):
                # If the path planner is PRM or AStar, reset it to update the world.
                self.robot.reset_path_planner()

        # Finalize path execution.
        self.reset_state()
        time.sleep(0.1)  # To ensure background threads get the end of the path.
        self.robot.last_nav_result = ExecutionResult(status=status, message=message)
        return self.robot.last_nav_result

    # With fog_hallways feature enabled,
    # validation of collisions in remaining path will be carried out based robot's recorded hallway states,
    # instead of of hallway states of the world's ground truth.
    def validate_remaining_path(self) -> None:
        """
        Validates the remaining path by checking collisions against the world.

        This function will set the `abort_execution` attribute to `True`,
        which cancels the main trajectory execution loop.
        """
        if (self.robot is None) or (self.traj is None):
            return

        while self.following_path and (not self.abort_execution):
            start_time = time.time()
            cur_pose = self.robot.get_pose()
            cur_time = self.current_traj_time

            # Get the waypoint index of the remaining path.
            for idx, t in enumerate(self.traj.t_pts):
                if t >= cur_time:
                    break
            if idx == self.traj.num_points() - 1:
                return

            # Collision check the remaining path.
            poses = [cur_pose]
            poses.extend(self.traj.poses[idx:])
            if len(poses) > 2:
                remaining_path = Path(poses=poses)
                if (self.robot.world is not None) and (
                    not self.robot.world.is_path_collision_free(
                        remaining_path,
                        step_dist=self.validation_step_dist,
                        fog_hallways=self.robot.fog_hallways,
                        recorded_closed_hallways=self.robot.recorded_closed_hallways,
                    )
                ):
                    self.robot.logger.warning(
                        "Remaining path is in collision. Aborting execution."
                    )
                    self.abort_execution = True

            time.sleep(max(0, self.validation_dt - (time.time() - start_time)))

    def detect_closed_hallway(self) -> None:
        """
        Get lidar measurement and determine if it's scanning a hallway.
        If yes, it would update the robot recorded_closed_hallways knowledge.
        It either remove (if it detects hallway is open) or add (if it detects hallway is close) the hallway
        into the robot's knowledge.
        """
        if (self.robot is None) or (self.robot.world is None):
            return

        if self.lidar_sensor_name is None:
            self.robot.logger.warning(
                "No lidar sensor name provided. Cannot detect closed hallway."
            )
            return

        lidar_sensor = self.robot.sensors.get(self.lidar_sensor_name)
        if isinstance(lidar_sensor, Lidar2D):
            # Get lidar angles range
            measured_angles = lidar_sensor.angles

        else:
            self.robot.logger.warning(
                "Lidar sensor is not a 2D lidar. Cannot detect closed hallway."
            )
            return

        while self.following_path and (not self.abort_execution):
            start_time = time.time()
            cur_pose = self.robot.get_pose()

            measured_lengths = lidar_sensor.get_measurement()
            analyse_pose = []
            for angle, length in zip(measured_angles, measured_lengths):
                if length < lidar_sensor.max_range_m:
                    # There are objects in lidar line of sight
                    adjusted_angle = angle + cur_pose.get_yaw()
                    x = cur_pose.x + length * np.cos(adjusted_angle)
                    y = cur_pose.y + length * np.sin(adjusted_angle)
                    analyse_pose.append((x, y))

            for pose in analyse_pose:
                for hallway in self.robot.world.hallways:
                    # Check if there are pose intersecting with hallway polygon
                    if shapely.intersects_xy(
                        hallway.internal_collision_polygon, pose[0], pose[1]
                    ):
                        # If yes, check if the hallway is closed
                        if not hallway.is_open:
                            if hallway not in self.robot.recorded_closed_hallways:
                                self.robot.recorded_closed_hallways.add(hallway)
                                self.robot.logger.info(
                                    f"Added {hallway.name} into closed knowledge."
                                )
                                self.hallway_states_updated = True
                                break
                        else:
                            if hallway in self.robot.recorded_closed_hallways:
                                self.robot.recorded_closed_hallways.remove(hallway)
                                self.robot.logger.info(
                                    f"Removed {hallway.name} from closed knowledge."
                                )
                                self.hallway_states_updated = True
                                break

            time.sleep(
                max(0, self.lidar_sensor_measurement_dt - (time.time() - start_time))
            )

    def to_dict(self) -> dict[str, Any]:
        """
        Serializes the path executor to a dictionary.

        :return: A dictionary containing the path executor information.
        """
        return {
            "type": "constant_velocity",
            "dt": self.dt,
            "linear_velocity": self.linear_velocity,
            "max_angular_velocity": self.max_angular_velocity,
            "validate_during_execution": self.validate_during_execution,
            "validation_dt": self.validation_dt,
            "validation_step_dist": self.validation_step_dist,
            "lidar_sensor_name": self.lidar_sensor_name,
            "lidar_sensor_measurement_dt": self.lidar_sensor_measurement_dt,
        }
