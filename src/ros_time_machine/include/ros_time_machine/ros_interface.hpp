// Copyright 2024 ROS Time Machine Contributors
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#ifndef ROS_TIME_MACHINE__ROS_INTERFACE_HPP_
#define ROS_TIME_MACHINE__ROS_INTERFACE_HPP_

#include <chrono>
#include <map>
#include <memory>
#include <string>

#include "rclcpp/rclcpp.hpp"
#include "ros_time_machine/buffer_manager.hpp"
#include "ros_time_machine/config_types.hpp"
#include "ros_time_machine/recorder.hpp"
#include "ros_time_machine_msgs/msg/buffer_stats.hpp"
#include "ros_time_machine_msgs/msg/event_trigger.hpp"
#include "ros_time_machine_msgs/msg/recording_status.hpp"
#include "ros_time_machine_msgs/srv/get_status.hpp"
#include "ros_time_machine_msgs/srv/list_snapshots.hpp"
#include "ros_time_machine_msgs/srv/pin_range.hpp"
#include "ros_time_machine_msgs/srv/release_pin.hpp"
#include "ros_time_machine_msgs/srv/trigger_snapshot.hpp"

namespace ros_time_machine
{

/**
 * @brief Topics and services through which detectors and tools drive the recorder
 *
 * - <event_trigger_topic> (EventTrigger): time-addressed triggers from any detector
 * - ~/trigger_snapshot (TriggerSnapshot): v1 service, treated as an event at "now"
 * - ~/pin_range, ~/release_pin: pin, then judge
 * - ~/list_snapshots, ~/get_status: introspection
 * - ~/buffer_stats (BufferStats): per-topic buffer health, incl. pins
 * - ~/recording_status (RecordingStatus): one message per finished clip
 */
class RosInterface
{
public:
  RosInterface(
    rclcpp::Node::SharedPtr node,
    const TimeMachineConfig & config,
    BufferManager & buffer_manager,
    Recorder & recorder);

  ~RosInterface();

private:
  using EventTriggerMsg = ros_time_machine_msgs::msg::EventTrigger;
  using BufferStatsMsg = ros_time_machine_msgs::msg::BufferStats;
  using RecordingStatusMsg = ros_time_machine_msgs::msg::RecordingStatus;
  using TriggerSnapshotSrv = ros_time_machine_msgs::srv::TriggerSnapshot;
  using PinRangeSrv = ros_time_machine_msgs::srv::PinRange;
  using ReleasePinSrv = ros_time_machine_msgs::srv::ReleasePin;
  using ListSnapshotsSrv = ros_time_machine_msgs::srv::ListSnapshots;
  using GetStatusSrv = ros_time_machine_msgs::srv::GetStatus;

  void onEventTrigger(const EventTriggerMsg::SharedPtr msg);

  void onTriggerSnapshot(
    const std::shared_ptr<TriggerSnapshotSrv::Request> request,
    std::shared_ptr<TriggerSnapshotSrv::Response> response);

  void onPinRange(
    const std::shared_ptr<PinRangeSrv::Request> request,
    std::shared_ptr<PinRangeSrv::Response> response);

  void onReleasePin(
    const std::shared_ptr<ReleasePinSrv::Request> request,
    std::shared_ptr<ReleasePinSrv::Response> response);

  void onListSnapshots(
    const std::shared_ptr<ListSnapshotsSrv::Request> request,
    std::shared_ptr<ListSnapshotsSrv::Response> response);

  void onGetStatus(
    const std::shared_ptr<GetStatusSrv::Request> request,
    std::shared_ptr<GetStatusSrv::Response> response);

  void publishBufferStats();

  void publishClipResult(const ClipResult & result);

  rclcpp::Node::SharedPtr node_;
  const TimeMachineConfig & config_;
  BufferManager & buffer_manager_;
  Recorder & recorder_;
  std::chrono::steady_clock::time_point start_time_;
  std::map<std::string, uint64_t> max_buffer_bytes_;  // topic -> configured limit

  rclcpp::Subscription<EventTriggerMsg>::SharedPtr event_sub_;
  rclcpp::Publisher<BufferStatsMsg>::SharedPtr stats_pub_;
  rclcpp::Publisher<RecordingStatusMsg>::SharedPtr status_pub_;
  rclcpp::Service<TriggerSnapshotSrv>::SharedPtr trigger_srv_;
  rclcpp::Service<PinRangeSrv>::SharedPtr pin_srv_;
  rclcpp::Service<ReleasePinSrv>::SharedPtr release_srv_;
  rclcpp::Service<ListSnapshotsSrv>::SharedPtr list_srv_;
  rclcpp::Service<GetStatusSrv>::SharedPtr status_srv_;
  rclcpp::TimerBase::SharedPtr stats_timer_;
};

}  // namespace ros_time_machine

#endif  // ROS_TIME_MACHINE__ROS_INTERFACE_HPP_
