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

#ifndef ROS_TIME_MACHINE__EVENT_SIDECAR_HPP_
#define ROS_TIME_MACHINE__EVENT_SIDECAR_HPP_

#include <cstdint>
#include <map>
#include <string>
#include <vector>

#include "ros_time_machine/trigger_queue.hpp"

namespace ros_time_machine
{

/**
 * @brief What a clip actually holds for one topic
 */
struct TopicCoverage
{
  std::string name;
  std::string type;
  size_t message_count = 0;
  int64_t first_ns = 0;
  int64_t last_ns = 0;
  bool pre_event_complete = true;  // Buffer still reached back to the window start
};

/**
 * @brief Everything recorded in a clip's event.json
 */
struct SidecarInfo
{
  std::string clip;              // Bag directory name
  std::string storage;           // "mcap" or "sqlite3"
  std::string time_source;       // "ros" or "sim"
  PendingClip window;            // Profile, window and merged events
  std::vector<TopicCoverage> topics;
  std::string robot_id;
  std::map<std::string, std::string> robot_metadata;
  int64_t written_at_ns = 0;     // Wall-clock time the clip was finalized
};

/// Schema identifier written into every sidecar
constexpr const char * kSidecarSchema = "rtm.event/1";

/// File name of the sidecar inside the bag directory
constexpr const char * kSidecarFileName = "event.json";

/**
 * @brief Format nanoseconds since epoch as UTC ISO 8601 with milliseconds
 */
std::string formatIso8601(int64_t ns);

/**
 * @brief Escape a string for inclusion in a JSON document (without quotes)
 */
std::string jsonEscape(const std::string & value);

/**
 * @brief Render the sidecar JSON document
 */
std::string buildSidecarJson(const SidecarInfo & info);

/**
 * @brief Write the sidecar atomically (temp file + rename)
 *
 * @return true on success; on failure error (if given) explains why
 */
bool writeSidecar(
  const std::string & path, const SidecarInfo & info, std::string * error = nullptr);

}  // namespace ros_time_machine

#endif  // ROS_TIME_MACHINE__EVENT_SIDECAR_HPP_
