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

#ifndef ROS_TIME_MACHINE__RECORDER_HPP_
#define ROS_TIME_MACHINE__RECORDER_HPP_

#include <string>
#include <memory>
#include <thread>
#include <queue>
#include <mutex>
#include <condition_variable>
#include <atomic>
#include <functional>
#include <set>

#include "rclcpp/rclcpp.hpp"
#include "rosbag2_cpp/writer.hpp"
#include "ros_time_machine/config_types.hpp"
#include "ros_time_machine/buffer_manager.hpp"
#include "ros_time_machine/event_sidecar.hpp"
#include "ros_time_machine/pin_manager.hpp"
#include "ros_time_machine/trigger_monitor.hpp"
#include "ros_time_machine/trigger_queue.hpp"

namespace ros_time_machine
{

/**
 * @brief Status of a recording job
 */
enum class RecordingStatus
{
  PENDING,      // Waiting in queue
  PRE_EVENT,    // Recording pre-event data
  POST_EVENT,   // Recording post-event data
  WRITING,      // Writing to disk
  COMPLETED,    // Successfully completed
  FAILED        // Failed to record
};

/**
 * @brief Information about a recording job
 */
struct RecordingJob
{
  std::string snapshot_name;
  PendingClip clip;                 // Window and every event merged into it
  SnapshotConfig snapshot_config;
  RecordingStatus status;
  std::string output_path;
  size_t message_count;
  std::string error_message;
};

/**
 * @brief Outcome of a finished recording job, reported to listeners
 */
struct ClipResult
{
  std::string snapshot_name;
  std::string output_path;
  RecordingStatus status;
  PendingClip clip;
  size_t message_count;
  size_t topic_count;
  std::string error_message;
};

using ClipCallback = std::function<void(const ClipResult &)>;

/**
 * @brief Result of a PinRange request
 */
struct PinResult
{
  bool success = false;
  uint64_t pin_id = 0;
  int64_t clamped_start_ns = 0;   // Earliest time all profile topics still hold data
  std::string message;
};

/**
 * @brief Recorder component for writing triggered snapshots to rosbag2 files
 *
 * Triggers are time-addressed: each names a window in robot time. The
 * recorder queues them (merging overlaps, enforcing budgets), protects their
 * data from eviction, and hands a clip to a worker thread once robot time has
 * passed the window end. Each clip is a rosbag2 directory with an event.json
 * sidecar explaining why it was saved.
 */
class Recorder
{
public:
  /**
   * @brief Construct a new Recorder
   * @param node Shared pointer to the ROS node
   * @param logger Logger for this component
   */
  Recorder(
    rclcpp::Node::SharedPtr node,
    rclcpp::Logger logger);

  /**
   * @brief Destructor - writes clips still pending, then stops worker threads
   */
  ~Recorder();

  /**
   * @brief Initialize the recorder with configuration
   * @param config The time machine configuration
   * @param buffer_manager Reference to the buffer manager
   * @return true if initialization successful
   */
  bool initialize(
    const TimeMachineConfig & config,
    BufferManager & buffer_manager);

  /**
   * @brief Handle a rule trigger (v1 path): save the profile's window around trigger_time
   * @param event The trigger event information
   * @param snapshot_config Configuration for this snapshot
   */
  void handleTrigger(
    const TriggerEvent & event,
    const SnapshotConfig & snapshot_config);

  /**
   * @brief Submit a time-addressed trigger
   *
   * Zero fields are resolved from the profile: an empty profile means the
   * first configured snapshot, event_ns 0 means now, start/end 0 mean
   * event -/+ the profile's pre/post_event_duration.
   */
  SubmitResult submitEvent(EventRequest request);

  /**
   * @brief Protect a range until a judge keeps or releases it
   */
  PinResult pinRange(
    int64_t start_ns, int64_t end_ns, const std::string & profile,
    double ttl_seconds, const std::string & reason);

  /**
   * @brief Resolve a pin; keep = true turns it into a clip described by metadata
   * @param message Set to a human-readable outcome
   * @return true if the pin existed and (when kept) its clip was accepted
   */
  bool releasePin(
    uint64_t pin_id, bool keep, const EventRequest & metadata, std::string & message);

  /**
   * @brief Called from a worker thread after each clip is written or fails
   */
  void setClipCallback(ClipCallback callback);

  /**
   * @brief Get statistics about active and completed recordings
   * @return Number of active recordings
   */
  size_t getActiveRecordingsCount() const;

  /**
   * @brief Get total number of completed recordings
   * @return Number of completed recordings
   */
  size_t getCompletedRecordingsCount() const;

  /**
   * @brief Get total number of failed recordings
   * @return Number of failed recordings
   */
  size_t getFailedRecordingsCount() const;

  /**
   * @brief Clips waiting for their window to close
   */
  size_t getPendingClipsCount() const;

  /**
   * @brief Pins awaiting a keep/release decision
   */
  size_t getActivePinsCount() const;

  /**
   * @brief Names of snapshots with a clip pending or being written
   */
  std::vector<std::string> getActiveSnapshotNames() const;

private:
  /**
   * @brief Worker thread function
   * @param thread_id ID of this worker thread
   */
  void workerThread(size_t thread_id);

  /**
   * @brief Process a single recording job
   * @param job The recording job to process
   */
  void processRecording(RecordingJob & job);

  /**
   * @brief Move clips whose window has closed to the workers; resolve expired pins
   */
  void dispatchTimerCallback();

  /**
   * @brief Queue a ready clip for the worker threads
   */
  void enqueueJob(const PendingClip & clip);

  /**
   * @brief Find a snapshot profile by name ("" = first configured snapshot)
   */
  const SnapshotConfig * findProfile(const std::string & name) const;

  /**
   * @brief Generate output file path for a recording
   * @param snapshot_name Name of the snapshot
   * @param trigger_time Time of the trigger
   * @param output_config Output configuration
   * @param label Event label for the {label} placeholder
   * @return Full path to output directory (never an existing one)
   */
  std::string generateOutputPath(
    const std::string & snapshot_name,
    const rclcpp::Time & trigger_time,
    const OutputConfig & output_config,
    const std::string & label);

  /**
   * @brief Write messages to a rosbag2 file
   * @param job The recording job
   * @param messages Map of topic names to their messages
   * @return true if writing succeeded
   */
  bool writeRosbag(
    RecordingJob & job,
    const std::map<std::string, std::vector<SerializedMessage>> & messages);

  rclcpp::Node::SharedPtr node_;
  rclcpp::Logger logger_;
  BufferManager * buffer_manager_;

  TimeMachineConfig config_;
  std::string time_source_;   // "sim" when use_sim_time, else "ros"

  // Time-addressed triggers and pins
  TriggerQueue trigger_queue_;
  PinManager pin_manager_;
  rclcpp::TimerBase::SharedPtr dispatch_timer_;

  // Worker thread management
  std::vector<std::thread> worker_threads_;
  std::queue<RecordingJob> job_queue_;
  mutable std::mutex queue_mutex_;
  std::condition_variable queue_cv_;
  std::atomic<bool> shutdown_;

  ClipCallback clip_callback_;
  std::mutex callback_mutex_;

  // Output directories chosen but not yet created by a worker
  std::set<std::string> reserved_paths_;
  std::mutex paths_mutex_;

  // Statistics
  std::atomic<size_t> active_recordings_;
  std::atomic<size_t> completed_recordings_;
  std::atomic<size_t> failed_recordings_;

  bool initialized_;
};

}  // namespace ros_time_machine

#endif  // ROS_TIME_MACHINE__RECORDER_HPP_
