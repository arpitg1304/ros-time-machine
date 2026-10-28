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

#ifndef ROS_TIME_MACHINE__TRIGGER_QUEUE_HPP_
#define ROS_TIME_MACHINE__TRIGGER_QUEUE_HPP_

#include <cstdint>
#include <deque>
#include <map>
#include <mutex>
#include <string>
#include <vector>

namespace ros_time_machine
{

/**
 * @brief A time-addressed request to save data around an event
 *
 * Every trigger names a window in robot time instead of meaning "now", so
 * detector latency becomes a buffer-sizing problem rather than a correctness
 * problem. Times are ROS time in nanoseconds; callers resolve defaults (zero
 * start/end) from the profile before submitting.
 */
struct EventRequest
{
  std::string profile;          // Snapshot profile (topics, output)
  int64_t event_ns = 0;         // When it happened
  int64_t start_ns = 0;         // Window to save
  int64_t end_ns = 0;
  std::string label;            // Short event type, e.g. "object_dropped"
  std::string description;      // Free-text caption from the detector
  float confidence = 1.0f;      // 0..1
  std::string source;           // "rule:/estop", "vlm:qwen3-vl-4b", "human", ...
  std::string source_version;   // Model + prompt hash, or rule id
  std::vector<std::string> tags;
  int64_t received_ns = 0;      // When Time Machine got the trigger (detection lag = this - event)
};

/**
 * @brief One clip to be written: a window plus every event merged into it
 */
struct PendingClip
{
  uint64_t id = 0;
  std::string profile;
  int64_t start_ns = 0;
  int64_t end_ns = 0;
  std::vector<EventRequest> events;  // In arrival order; never empty

  /**
   * @brief The event the clip is named after (highest confidence, earliest on ties)
   */
  const EventRequest & primary() const;
};

/**
 * @brief Limits that keep a trigger storm from filling the disk
 */
struct TriggerBudget
{
  uint32_t max_clips_per_hour = 0;     // Across all profiles; 0 = unlimited
  double merge_gap_seconds = 0.0;      // Merge windows closer than this
  double max_clip_duration = 300.0;    // Never grow a merged clip past this
};

enum class SubmitOutcome
{
  QUEUED,               // New clip created
  MERGED,               // Folded into an existing pending clip
  REJECTED_INVALID,     // Bad window
  REJECTED_RATE_LIMIT,  // Profile's max_triggers_per_minute exceeded
  REJECTED_BUDGET       // Global clips-per-hour budget exceeded
};

struct SubmitResult
{
  SubmitOutcome outcome = SubmitOutcome::REJECTED_INVALID;
  uint64_t clip_id = 0;
  std::string message;

  bool accepted() const
  {
    return outcome == SubmitOutcome::QUEUED || outcome == SubmitOutcome::MERGED;
  }
};

/**
 * @brief Accepts time-addressed triggers, merges overlaps, enforces budgets
 *
 * A clip becomes ready once robot time has passed its end (plus a settle
 * delay for in-flight messages). Ready clips stay "in flight" until the writer
 * calls complete(), so covers() keeps protecting their data from eviction for
 * the whole time between trigger and write.
 */
class TriggerQueue
{
public:
  explicit TriggerQueue(TriggerBudget budget = TriggerBudget());

  void setBudget(const TriggerBudget & budget);

  /**
   * @brief Limit how many new clips a profile may create per minute (0 = unlimited)
   */
  void setProfileRateLimit(const std::string & profile, uint32_t max_per_minute);

  SubmitResult submit(const EventRequest & request, int64_t now_ns);

  /**
   * @brief Hand over every clip whose window has closed
   *
   * If robot time jumps backwards (bag replay looped, sim restarted), every
   * pending clip is released immediately rather than waiting forever.
   */
  std::vector<PendingClip> popReady(int64_t now_ns, int64_t settle_ns = 0);

  /**
   * @brief Mark an in-flight clip as written (or failed)
   */
  void complete(uint64_t clip_id);

  /**
   * @brief True if a pending or in-flight clip needs this timestamp
   */
  bool covers(int64_t t_ns) const;

  /**
   * @brief Profiles with a clip pending or in flight (each listed once)
   */
  std::vector<std::string> activeProfiles() const;

  size_t pendingCount() const;
  size_t inFlightCount() const;
  uint64_t rejectedCount() const;
  uint64_t mergedCount() const;

private:
  bool allowNewClip(const std::string & profile, int64_t now_ns, SubmitResult & result);

  mutable std::mutex mutex_;
  TriggerBudget budget_;
  std::vector<PendingClip> pending_;
  std::map<uint64_t, PendingClip> in_flight_;
  std::map<std::string, uint32_t> rate_limits_;
  std::map<std::string, std::deque<int64_t>> profile_history_;
  std::deque<int64_t> clip_history_;
  uint64_t next_id_ = 1;
  uint64_t rejected_ = 0;
  uint64_t merged_ = 0;
  int64_t last_now_ns_ = 0;
};

}  // namespace ros_time_machine

#endif  // ROS_TIME_MACHINE__TRIGGER_QUEUE_HPP_
