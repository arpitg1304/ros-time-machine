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

#include <gtest/gtest.h>

#include "ros_time_machine/trigger_queue.hpp"

using ros_time_machine::EventRequest;
using ros_time_machine::SubmitOutcome;
using ros_time_machine::TriggerBudget;
using ros_time_machine::TriggerQueue;

namespace
{
constexpr int64_t kSec = 1000000000LL;

EventRequest event(
  double event_s, double pre, double post, const std::string & profile = "manipulation",
  const std::string & label = "object_dropped", float confidence = 0.8f)
{
  EventRequest request;
  request.profile = profile;
  request.event_ns = static_cast<int64_t>(event_s * kSec);
  request.start_ns = static_cast<int64_t>((event_s - pre) * kSec);
  request.end_ns = static_cast<int64_t>((event_s + post) * kSec);
  request.label = label;
  request.confidence = confidence;
  request.source = "test";
  return request;
}
}  // namespace

TEST(TriggerQueueTest, PastEventWaitsOnlyForWindowEnd)
{
  // The plan's Phase 0 "done when": an event 4 s in the past gets the right window
  TriggerQueue queue;
  auto result = queue.submit(event(96.0, 5.0, 3.0), 100 * kSec);
  ASSERT_EQ(result.outcome, SubmitOutcome::QUEUED);

  EXPECT_TRUE(queue.popReady(98 * kSec).empty());
  auto ready = queue.popReady(99 * kSec);
  ASSERT_EQ(ready.size(), 1u);
  EXPECT_EQ(ready[0].start_ns, 91 * kSec);
  EXPECT_EQ(ready[0].end_ns, 99 * kSec);
  EXPECT_EQ(ready[0].primary().event_ns, 96 * kSec);
}

TEST(TriggerQueueTest, SettleDelaysReadiness)
{
  TriggerQueue queue;
  queue.submit(event(10.0, 1.0, 1.0), 10 * kSec);
  EXPECT_TRUE(queue.popReady(11 * kSec, kSec / 2).empty());
  EXPECT_EQ(queue.popReady(11 * kSec + kSec / 2, kSec / 2).size(), 1u);
}

TEST(TriggerQueueTest, InvalidWindowRejected)
{
  TriggerQueue queue;
  EventRequest bad = event(10.0, 0.0, 0.0);
  EXPECT_EQ(queue.submit(bad, 10 * kSec).outcome, SubmitOutcome::REJECTED_INVALID);
  EXPECT_EQ(queue.rejectedCount(), 1u);
}

TEST(TriggerQueueTest, OverlongWindowRejected)
{
  TriggerBudget budget;
  budget.max_clip_duration = 10.0;
  TriggerQueue queue(budget);
  EXPECT_EQ(
    queue.submit(event(100.0, 8.0, 8.0), 100 * kSec).outcome, SubmitOutcome::REJECTED_INVALID);
  EXPECT_EQ(queue.submit(event(100.0, 5.0, 5.0), 100 * kSec).outcome, SubmitOutcome::QUEUED);
}

TEST(TriggerQueueTest, OverlappingWindowsMerge)
{
  TriggerQueue queue;
  auto first = queue.submit(event(10.0, 5.0, 3.0, "manipulation", "a", 0.6f), 10 * kSec);
  auto second = queue.submit(event(12.0, 5.0, 3.0, "manipulation", "b", 0.9f), 12 * kSec);
  EXPECT_EQ(first.outcome, SubmitOutcome::QUEUED);
  EXPECT_EQ(second.outcome, SubmitOutcome::MERGED);
  EXPECT_EQ(second.clip_id, first.clip_id);
  EXPECT_EQ(queue.pendingCount(), 1u);
  EXPECT_EQ(queue.mergedCount(), 1u);

  auto ready = queue.popReady(100 * kSec);
  ASSERT_EQ(ready.size(), 1u);
  EXPECT_EQ(ready[0].start_ns, 5 * kSec);
  EXPECT_EQ(ready[0].end_ns, 15 * kSec);
  ASSERT_EQ(ready[0].events.size(), 2u);
  EXPECT_EQ(ready[0].primary().label, "b");  // Highest confidence names the clip
}

TEST(TriggerQueueTest, DifferentProfilesDoNotMerge)
{
  TriggerQueue queue;
  queue.submit(event(10.0, 5.0, 3.0, "manipulation"), 10 * kSec);
  auto other = queue.submit(event(10.0, 5.0, 3.0, "safety"), 10 * kSec);
  EXPECT_EQ(other.outcome, SubmitOutcome::QUEUED);
  EXPECT_EQ(queue.pendingCount(), 2u);
}

TEST(TriggerQueueTest, MergeGapJoinsNearbyWindows)
{
  TriggerBudget budget;
  budget.merge_gap_seconds = 2.0;
  TriggerQueue queue(budget);
  queue.submit(event(10.0, 1.0, 1.0), 10 * kSec);           // [9, 11]
  auto near = queue.submit(event(14.0, 1.0, 1.0), 14 * kSec);  // [13, 15], gap 2 s
  EXPECT_EQ(near.outcome, SubmitOutcome::MERGED);
  auto far = queue.submit(event(30.0, 1.0, 1.0), 30 * kSec);
  EXPECT_EQ(far.outcome, SubmitOutcome::QUEUED);
}

TEST(TriggerQueueTest, MaxClipDurationStopsMerging)
{
  TriggerBudget budget;
  budget.max_clip_duration = 10.0;
  TriggerQueue queue(budget);
  queue.submit(event(10.0, 4.0, 4.0), 10 * kSec);           // [6, 14]
  auto grown = queue.submit(event(16.0, 4.0, 4.0), 16 * kSec);  // would be [6, 20]
  EXPECT_EQ(grown.outcome, SubmitOutcome::QUEUED);
  EXPECT_EQ(queue.pendingCount(), 2u);
}

TEST(TriggerQueueTest, ProfileRateLimitCountsNewClipsOnly)
{
  TriggerQueue queue;
  queue.setProfileRateLimit("manipulation", 1);
  EXPECT_EQ(queue.submit(event(10.0, 1.0, 1.0), 10 * kSec).outcome, SubmitOutcome::QUEUED);
  // Overlapping: merged, not rate limited
  EXPECT_EQ(queue.submit(event(10.5, 1.0, 1.0), 10 * kSec).outcome, SubmitOutcome::MERGED);
  // New clip within the same minute: rejected
  EXPECT_EQ(
    queue.submit(event(30.0, 1.0, 1.0), 30 * kSec).outcome,
    SubmitOutcome::REJECTED_RATE_LIMIT);
  // A minute later it is allowed again
  EXPECT_EQ(queue.submit(event(80.0, 1.0, 1.0), 80 * kSec).outcome, SubmitOutcome::QUEUED);
}

TEST(TriggerQueueTest, HourlyBudgetAcrossProfiles)
{
  TriggerBudget budget;
  budget.max_clips_per_hour = 2;
  TriggerQueue queue(budget);
  EXPECT_TRUE(queue.submit(event(10.0, 1.0, 1.0, "a"), 10 * kSec).accepted());
  EXPECT_TRUE(queue.submit(event(10.0, 1.0, 1.0, "b"), 10 * kSec).accepted());
  EXPECT_EQ(
    queue.submit(event(10.0, 1.0, 1.0, "c"), 10 * kSec).outcome,
    SubmitOutcome::REJECTED_BUDGET);
  EXPECT_TRUE(queue.submit(event(4000.0, 1.0, 1.0, "c"), 4000 * kSec).accepted());
}

TEST(TriggerQueueTest, CoversPendingAndInFlightUntilComplete)
{
  TriggerQueue queue;
  auto result = queue.submit(event(10.0, 2.0, 2.0), 10 * kSec);
  EXPECT_TRUE(queue.covers(9 * kSec));
  EXPECT_FALSE(queue.covers(13 * kSec));

  auto ready = queue.popReady(12 * kSec);
  ASSERT_EQ(ready.size(), 1u);
  EXPECT_EQ(queue.pendingCount(), 0u);
  EXPECT_EQ(queue.inFlightCount(), 1u);
  EXPECT_TRUE(queue.covers(9 * kSec));  // Writer has not read the data yet

  queue.complete(result.clip_id);
  EXPECT_FALSE(queue.covers(9 * kSec));
  EXPECT_EQ(queue.inFlightCount(), 0u);
}

TEST(TriggerQueueTest, ActiveProfilesListedOnce)
{
  TriggerQueue queue;
  queue.submit(event(10.0, 1.0, 1.0, "a"), 10 * kSec);
  queue.submit(event(50.0, 1.0, 1.0, "a"), 50 * kSec);
  queue.submit(event(10.0, 1.0, 1.0, "b"), 10 * kSec);
  queue.popReady(12 * kSec);  // "a" and "b" first clips now in flight
  auto profiles = queue.activeProfiles();
  EXPECT_EQ(profiles.size(), 2u);
}

TEST(TriggerQueueTest, InFlightClipsDoNotAbsorbNewEvents)
{
  TriggerQueue queue;
  queue.submit(event(10.0, 2.0, 2.0), 10 * kSec);
  queue.popReady(12 * kSec);
  auto late = queue.submit(event(11.0, 2.0, 2.0), 12 * kSec);
  EXPECT_EQ(late.outcome, SubmitOutcome::QUEUED);
}

TEST(TriggerQueueTest, ClockResetReleasesPendingClips)
{
  // Bag replay looped: robot time jumps from 500 s back to 1 s
  TriggerQueue queue;
  queue.submit(event(499.0, 2.0, 10.0), 499 * kSec);
  EXPECT_TRUE(queue.popReady(500 * kSec).empty());
  EXPECT_EQ(queue.popReady(1 * kSec).size(), 1u);
}

int main(int argc, char ** argv)
{
  testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
