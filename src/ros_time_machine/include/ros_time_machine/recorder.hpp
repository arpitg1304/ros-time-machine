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

#include "rclcpp/rclcpp.hpp"
#include "rosbag2_cpp/writer.hpp"
#include "ros_time_machine/config_types.hpp"
#include "ros_time_machine/buffer_manager.hpp"
#include "ros_time_machine/trigger_monitor.hpp"

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
  TriggerEvent trigger_event;
  SnapshotConfig snapshot_config;
  rclcpp::Time start_time;
  rclcpp::Time end_time;
  RecordingStatus status;
  std::string output_path;
  size_t message_count;
};

/**
 * @brief Recorder component for writing triggered snapshots to rosbag2 files
 *
 * This class manages recording jobs triggered by events. It retrieves
 * buffered messages from the BufferManager and writes them to rosbag2 files.
 * Recording happens asynchronously in worker threads.
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
   * @brief Destructor - stops worker threads
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
   * @brief Handle a trigger event and start recording
   * @param event The trigger event information
   * @param snapshot_config Configuration for this snapshot
   */
  void handleTrigger(
    const TriggerEvent & event,
    const SnapshotConfig & snapshot_config);

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
   * @brief Generate output file path for a recording
   * @param snapshot_name Name of the snapshot
   * @param trigger_time Time of the trigger
   * @param output_config Output configuration
   * @return Full path to output directory
   */
  std::string generateOutputPath(
    const std::string & snapshot_name,
    const rclcpp::Time & trigger_time,
    const OutputConfig & output_config);

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

  // Worker thread management
  std::vector<std::thread> worker_threads_;
  std::queue<RecordingJob> job_queue_;
  mutable std::mutex queue_mutex_;
  std::condition_variable queue_cv_;
  std::atomic<bool> shutdown_;

  // Statistics
  std::atomic<size_t> active_recordings_;
  std::atomic<size_t> completed_recordings_;
  std::atomic<size_t> failed_recordings_;

  bool initialized_;
};

}  // namespace ros_time_machine

#endif  // ROS_TIME_MACHINE__RECORDER_HPP_
