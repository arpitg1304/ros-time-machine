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

#include "ros_time_machine/trigger_queue.hpp"

#include <algorithm>

namespace ros_time_machine
{

namespace
{
constexpr int64_t kNsPerSecond = 1000000000LL;
constexpr int64_t kMinute = 60 * kNsPerSecond;
constexpr int64_t kHour = 60 * kMinute;
// Robot time moving backwards by more than this is treated as a clock reset
constexpr int64_t kClockJumpTolerance = kNsPerSecond;

int64_t toNs(double seconds)
{
  return static_cast<int64_t>(seconds * 1e9);
}

void pruneOlderThan(std::deque<int64_t> & history, int64_t cutoff)
{
  while (!history.empty() && history.front() <= cutoff) {
    history.pop_front();
  }
}
}  // namespace

const EventRequest & PendingClip::primary() const
{
  const EventRequest * best = &events.front();
  for (const auto & event : events) {
    if (event.confidence > best->confidence) {
      best = &event;
    }
  }
  return *best;
}

TriggerQueue::TriggerQueue(TriggerBudget budget)
  : budget_(budget)
{
}

void TriggerQueue::setBudget(const TriggerBudget & budget)
{
  std::lock_guard<std::mutex> lock(mutex_);
  budget_ = budget;
}

void TriggerQueue::setProfileRateLimit(const std::string & profile, uint32_t max_per_minute)
{
  std::lock_guard<std::mutex> lock(mutex_);
  rate_limits_[profile] = max_per_minute;
}

SubmitResult TriggerQueue::submit(const EventRequest & request, int64_t now_ns)
{
  std::lock_guard<std::mutex> lock(mutex_);
  SubmitResult result;

  if (request.end_ns <= request.start_ns) {
    result.outcome = SubmitOutcome::REJECTED_INVALID;
    result.message = "window end must be after start";
    rejected_++;
    return result;
  }

  const int64_t gap = toNs(budget_.merge_gap_seconds);
  const int64_t max_duration = toNs(budget_.max_clip_duration);
  if (max_duration > 0 && request.end_ns - request.start_ns > max_duration) {
    result.outcome = SubmitOutcome::REJECTED_INVALID;
    result.message = "window longer than max_clip_duration";
    rejected_++;
    return result;
  }

  // Merge into an overlapping pending clip of the same profile
  for (auto & clip : pending_) {
    if (clip.profile != request.profile) {
      continue;
    }
    bool overlaps = request.start_ns <= clip.end_ns + gap &&
      request.end_ns >= clip.start_ns - gap;
    int64_t merged_start = std::min(clip.start_ns, request.start_ns);
    int64_t merged_end = std::max(clip.end_ns, request.end_ns);
    if (overlaps && (max_duration <= 0 || merged_end - merged_start <= max_duration)) {
      clip.start_ns = merged_start;
      clip.end_ns = merged_end;
      clip.events.push_back(request);
      merged_++;
      result.outcome = SubmitOutcome::MERGED;
      result.clip_id = clip.id;
      result.message = "merged into pending clip (" + std::to_string(clip.events.size()) +
        " events)";
      return result;
    }
  }

  if (!allowNewClip(request.profile, now_ns, result)) {
    rejected_++;
    return result;
  }

  PendingClip clip;
  clip.id = next_id_++;
  clip.profile = request.profile;
  clip.start_ns = request.start_ns;
  clip.end_ns = request.end_ns;
  clip.events.push_back(request);
  pending_.push_back(clip);

  result.outcome = SubmitOutcome::QUEUED;
  result.clip_id = clip.id;
  result.message = "queued";
  return result;
}

bool TriggerQueue::allowNewClip(
  const std::string & profile, int64_t now_ns, SubmitResult & result)
{
  auto limit_it = rate_limits_.find(profile);
  auto & profile_history = profile_history_[profile];
  pruneOlderThan(profile_history, now_ns - kMinute);
  if (limit_it != rate_limits_.end() && limit_it->second > 0 &&
    profile_history.size() >= limit_it->second)
  {
    result.outcome = SubmitOutcome::REJECTED_RATE_LIMIT;
    result.message = "profile '" + profile + "' is rate limited (" +
      std::to_string(limit_it->second) + "/min)";
    return false;
  }

  pruneOlderThan(clip_history_, now_ns - kHour);
  if (budget_.max_clips_per_hour > 0 && clip_history_.size() >= budget_.max_clips_per_hour) {
    result.outcome = SubmitOutcome::REJECTED_BUDGET;
    result.message = "clip budget exhausted (" + std::to_string(budget_.max_clips_per_hour) +
      "/hour)";
    return false;
  }

  profile_history.push_back(now_ns);
  clip_history_.push_back(now_ns);
  return true;
}

std::vector<PendingClip> TriggerQueue::popReady(int64_t now_ns, int64_t settle_ns)
{
  std::lock_guard<std::mutex> lock(mutex_);

  bool clock_reset = last_now_ns_ != 0 && now_ns + kClockJumpTolerance < last_now_ns_;
  if (clock_reset) {
    // Rate-limit history is in the old timeline too
    profile_history_.clear();
    clip_history_.clear();
  }
  last_now_ns_ = now_ns;

  std::vector<PendingClip> ready;
  for (auto it = pending_.begin(); it != pending_.end(); ) {
    if (clock_reset || it->end_ns + settle_ns <= now_ns) {
      in_flight_[it->id] = *it;
      ready.push_back(std::move(*it));
      it = pending_.erase(it);
    } else {
      ++it;
    }
  }
  return ready;
}

void TriggerQueue::complete(uint64_t clip_id)
{
  std::lock_guard<std::mutex> lock(mutex_);
  in_flight_.erase(clip_id);
}

bool TriggerQueue::covers(int64_t t_ns) const
{
  std::lock_guard<std::mutex> lock(mutex_);
  for (const auto & clip : pending_) {
    if (t_ns >= clip.start_ns && t_ns <= clip.end_ns) {
      return true;
    }
  }
  for (const auto & [_, clip] : in_flight_) {
    if (t_ns >= clip.start_ns && t_ns <= clip.end_ns) {
      return true;
    }
  }
  return false;
}

std::vector<std::string> TriggerQueue::activeProfiles() const
{
  std::lock_guard<std::mutex> lock(mutex_);
  std::vector<std::string> profiles;
  auto add = [&profiles](const std::string & profile) {
      if (std::find(profiles.begin(), profiles.end(), profile) == profiles.end()) {
        profiles.push_back(profile);
      }
    };
  for (const auto & clip : pending_) {
    add(clip.profile);
  }
  for (const auto & [_, clip] : in_flight_) {
    add(clip.profile);
  }
  return profiles;
}

size_t TriggerQueue::pendingCount() const
{
  std::lock_guard<std::mutex> lock(mutex_);
  return pending_.size();
}

size_t TriggerQueue::inFlightCount() const
{
  std::lock_guard<std::mutex> lock(mutex_);
  return in_flight_.size();
}

uint64_t TriggerQueue::rejectedCount() const
{
  std::lock_guard<std::mutex> lock(mutex_);
  return rejected_;
}

uint64_t TriggerQueue::mergedCount() const
{
  std::lock_guard<std::mutex> lock(mutex_);
  return merged_;
}

}  // namespace ros_time_machine
