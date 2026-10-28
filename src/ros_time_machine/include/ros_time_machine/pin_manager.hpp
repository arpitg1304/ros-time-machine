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

#ifndef ROS_TIME_MACHINE__PIN_MANAGER_HPP_
#define ROS_TIME_MACHINE__PIN_MANAGER_HPP_

#include <cstdint>
#include <map>
#include <mutex>
#include <optional>
#include <string>
#include <vector>

namespace ros_time_machine
{

/**
 * @brief A protected buffer range awaiting a keep/release decision
 *
 * All times are robot ROS time in nanoseconds.
 */
struct Pin
{
  uint64_t id = 0;
  int64_t start_ns = 0;
  int64_t end_ns = 0;
  int64_t expires_ns = 0;   // Pin resolves by its profile's timeout policy after this
  std::string profile;      // Snapshot profile used if the pin is kept
  std::string reason;
};

/**
 * @brief Tracks pinned time ranges ("pin, then judge")
 *
 * A cheap local signal pins a range immediately; a slower judge (VLM, human)
 * later keeps or releases it. While a pin exists, buffers retain the messages
 * it covers instead of evicting them. The manager holds no ROS types so it can
 * be unit tested and used from any thread.
 */
class PinManager
{
public:
  /**
   * @brief Pin a range
   *
   * @param start_ns Range start (robot time)
   * @param end_ns Range end (robot time); swapped with start if reversed
   * @param ttl_seconds Seconds until the pin times out (<= 0: default_ttl_seconds)
   * @param now_ns Current robot time
   * @return uint64_t Pin id (never 0)
   */
  uint64_t add(
    int64_t start_ns, int64_t end_ns, double ttl_seconds, int64_t now_ns,
    const std::string & profile = "", const std::string & reason = "");

  /**
   * @brief Look up a pin without removing it
   */
  std::optional<Pin> get(uint64_t pin_id) const;

  /**
   * @brief Remove a pin and return it (for a keep/release decision)
   *
   * @return std::optional<Pin> The pin, or nullopt if unknown or already resolved
   */
  std::optional<Pin> take(uint64_t pin_id);

  /**
   * @brief Remove and return every pin whose TTL has passed
   */
  std::vector<Pin> takeExpired(int64_t now_ns);

  /**
   * @brief Every pin whose TTL has passed, left in place
   *
   * Use this when an expired pin is kept: queue its clip first, then take() the
   * pin, so its data is protected the whole time.
   */
  std::vector<Pin> expired(int64_t now_ns) const;

  /**
   * @brief True if any pin covers the timestamp (used as a buffer eviction guard)
   */
  bool covers(int64_t t_ns) const;

  size_t activeCount() const;

  std::vector<Pin> list() const;

  void setDefaultTtl(double seconds);

private:
  mutable std::mutex mutex_;
  std::map<uint64_t, Pin> pins_;
  uint64_t next_id_ = 1;
  double default_ttl_seconds_ = 30.0;
};

}  // namespace ros_time_machine

#endif  // ROS_TIME_MACHINE__PIN_MANAGER_HPP_
