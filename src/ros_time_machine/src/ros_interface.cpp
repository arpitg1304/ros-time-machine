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

#include "ros_time_machine/ros_interface.hpp"

#include <algorithm>
#include <functional>

namespace ros_time_machine
{

namespace
{
bool validStamp(const builtin_interfaces::msg::Time & stamp)
{
  // rclcpp::Time throws on negative seconds; a bad detector must not take the node down
  return stamp.sec >= 0;
}

int64_t toNs(const builtin_interfaces::msg::Time & stamp)
{
  return rclcpp::Time(stamp, RCL_ROS_TIME).nanoseconds();
}
}  // namespace

RosInterface::RosInterface(
  rclcpp::Node::SharedPtr node,
  const TimeMachineConfig & config,
  BufferManager & buffer_manager,
  Recorder & recorder)
  : node_(node),
    config_(config),
    buffer_manager_(buffer_manager),
    recorder_(recorder),
    start_time_(std::chrono::steady_clock::now())
{
  using std::placeholders::_1;
  using std::placeholders::_2;

  for (const auto & snapshot : config.snapshots) {
    uint64_t bytes = snapshot.recording.max_buffer_size * 1024 * 1024;
    for (const auto & topic : snapshot.recording.topics) {
      max_buffer_bytes_[topic] = std::max(max_buffer_bytes_[topic], bytes);
    }
  }

  // Detectors may fire bursts; keep every trigger
  event_sub_ = node_->create_subscription<EventTriggerMsg>(
    config.global.event_trigger_topic, rclcpp::QoS(100).reliable(),
    std::bind(&RosInterface::onEventTrigger, this, _1));

  status_pub_ = node_->create_publisher<RecordingStatusMsg>(
    "~/recording_status", rclcpp::QoS(50).reliable());

  trigger_srv_ = node_->create_service<TriggerSnapshotSrv>(
    "~/trigger_snapshot", std::bind(&RosInterface::onTriggerSnapshot, this, _1, _2));
  pin_srv_ = node_->create_service<PinRangeSrv>(
    "~/pin_range", std::bind(&RosInterface::onPinRange, this, _1, _2));
  release_srv_ = node_->create_service<ReleasePinSrv>(
    "~/release_pin", std::bind(&RosInterface::onReleasePin, this, _1, _2));
  list_srv_ = node_->create_service<ListSnapshotsSrv>(
    "~/list_snapshots", std::bind(&RosInterface::onListSnapshots, this, _1, _2));
  status_srv_ = node_->create_service<GetStatusSrv>(
    "~/get_status", std::bind(&RosInterface::onGetStatus, this, _1, _2));

  if (config.global.publish_stats) {
    stats_pub_ = node_->create_publisher<BufferStatsMsg>("~/buffer_stats", rclcpp::QoS(50));
    auto period = std::chrono::duration<double>(1.0 / config.global.stats_publish_rate);
    stats_timer_ = node_->create_wall_timer(
      std::chrono::duration_cast<std::chrono::nanoseconds>(period),
      std::bind(&RosInterface::publishBufferStats, this));
  }

  recorder_.setClipCallback(std::bind(&RosInterface::publishClipResult, this, _1));

  RCLCPP_INFO(node_->get_logger(), "Listening for EventTrigger on %s",
              event_sub_->get_topic_name());
}

RosInterface::~RosInterface()
{
  // The recorder outlives us and still writes clips during shutdown
  recorder_.setClipCallback(nullptr);
}

void RosInterface::onEventTrigger(const EventTriggerMsg::SharedPtr msg)
{
  if (!validStamp(msg->event_stamp) || !validStamp(msg->start) || !validStamp(msg->end)) {
    RCLCPP_WARN(node_->get_logger(), "EventTrigger '%s' from %s ignored: negative timestamp",
                msg->label.c_str(), msg->source.c_str());
    return;
  }
  EventRequest request;
  request.profile = msg->profile;
  request.event_ns = toNs(msg->event_stamp);
  request.start_ns = toNs(msg->start);
  request.end_ns = toNs(msg->end);
  request.label = msg->label;
  request.description = msg->description;
  request.confidence = msg->confidence;
  request.source = msg->source.empty() ? "topic" : msg->source;
  request.source_version = msg->source_version;
  request.tags = msg->tags;
  recorder_.submitEvent(request);
}

void RosInterface::onTriggerSnapshot(
  const std::shared_ptr<TriggerSnapshotSrv::Request> request,
  std::shared_ptr<TriggerSnapshotSrv::Response> response)
{
  EventRequest event;
  event.profile = request->snapshot_name;
  event.source = "service";
  auto result = recorder_.submitEvent(event);
  response->success = result.accepted();
  response->message = result.accepted() ?
    "clip " + std::to_string(result.clip_id) + " " + result.message +
    "; path is reported on ~/recording_status when written" :
    result.message;
  response->output_path = "";
}

void RosInterface::onPinRange(
  const std::shared_ptr<PinRangeSrv::Request> request,
  std::shared_ptr<PinRangeSrv::Response> response)
{
  if (!validStamp(request->start) || !validStamp(request->end)) {
    response->success = false;
    response->message = "negative timestamp";
    return;
  }
  auto result = recorder_.pinRange(
    toNs(request->start), toNs(request->end), request->profile,
    request->ttl_seconds, request->reason);
  response->success = result.success;
  response->pin_id = result.pin_id;
  response->clamped_start = rclcpp::Time(result.clamped_start_ns, RCL_ROS_TIME);
  response->message = result.message;
}

void RosInterface::onReleasePin(
  const std::shared_ptr<ReleasePinSrv::Request> request,
  std::shared_ptr<ReleasePinSrv::Response> response)
{
  EventRequest metadata;
  metadata.label = request->label;
  metadata.description = request->description;
  metadata.confidence = request->confidence;
  metadata.source = request->source;
  std::string message;
  response->success = recorder_.releasePin(request->pin_id, request->keep, metadata, message);
  response->message = message;
}

void RosInterface::onListSnapshots(
  const std::shared_ptr<ListSnapshotsSrv::Request>,
  std::shared_ptr<ListSnapshotsSrv::Response> response)
{
  for (const auto & snapshot : config_.snapshots) {
    response->snapshot_names.push_back(snapshot.name);
    response->descriptions.push_back(snapshot.description);
  }
  response->success = true;
  response->message = std::to_string(config_.snapshots.size()) + " snapshot profile(s)";
}

void RosInterface::onGetStatus(
  const std::shared_ptr<GetStatusSrv::Request>,
  std::shared_ptr<GetStatusSrv::Response> response)
{
  response->success = true;
  response->active_recordings = recorder_.getActiveSnapshotNames();
  response->total_buffer_memory = buffer_manager_.getTotalMemoryUsage();
  response->num_snapshots = static_cast<uint32_t>(config_.snapshots.size());
  response->uptime_seconds = std::chrono::duration<double>(
    std::chrono::steady_clock::now() - start_time_).count();
  response->message =
    std::to_string(recorder_.getPendingClipsCount()) + " pending clip(s), " +
    std::to_string(recorder_.getActivePinsCount()) + " active pin(s), " +
    std::to_string(recorder_.getCompletedRecordingsCount()) + " written, " +
    std::to_string(recorder_.getFailedRecordingsCount()) + " failed";
}

void RosInterface::publishBufferStats()
{
  auto active_pins = static_cast<uint32_t>(recorder_.getActivePinsCount());
  for (const auto & stats : buffer_manager_.getAllStats()) {
    BufferStatsMsg msg;
    msg.topic_name = stats.topic_name;
    msg.message_count = stats.message_count;
    msg.memory_bytes = stats.estimated_memory_bytes;
    msg.max_buffer_bytes = max_buffer_bytes_[stats.topic_name];
    msg.utilization_percent = static_cast<float>(stats.utilization_percent);
    msg.messages_dropped = stats.messages_dropped;
    msg.oldest_message_time = stats.oldest_message_time;
    msg.newest_message_time = stats.newest_message_time;
    msg.pinned_message_count = stats.pinned_message_count;
    msg.active_pins = active_pins;
    stats_pub_->publish(msg);
  }
}

void RosInterface::publishClipResult(const ClipResult & result)
{
  RecordingStatusMsg msg;
  msg.status = result.status == RecordingStatus::COMPLETED ?
    RecordingStatusMsg::STATUS_COMPLETED : RecordingStatusMsg::STATUS_FAILED;
  msg.snapshot_name = result.snapshot_name;
  msg.output_path = result.output_path;
  msg.trigger_time = rclcpp::Time(result.clip.primary().event_ns, RCL_ROS_TIME);
  msg.num_topics = static_cast<uint32_t>(result.topic_count);
  msg.buffer_size_bytes = 0;
  msg.error_message = result.error_message;
  status_pub_->publish(msg);
}

}  // namespace ros_time_machine
