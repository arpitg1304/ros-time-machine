# ROS Time Machine - Architecture

## Overview

Event-triggered recording system for ROS2 that captures sensor data snapshots with pre-event buffering and post-event recording capabilities.

## System Flow

### High-Level Data Flow

```mermaid
graph LR
    subgraph "1. STARTUP"
        A[Start Node] --> B[Load Config]
        B --> C{Valid?}
        C -->|No| D[Exit]
        C -->|Yes| E[Init Components]
    end

    subgraph "2. BUFFERING"
        F[ROS Topics] --> G[Buffer Manager]
        G --> H[Circular Buffers<br/>Always Recording]
    end

    subgraph "3. TRIGGER"
        F --> I[Trigger Monitor]
        I --> J{Event?}
        J -->|Yes| K[Signal Recorder]
    end

    subgraph "4. RECORDING"
        K --> L[Dump Buffer<br/>t-N to t0]
        L --> M[Record Live<br/>t0 to t+M]
        M --> N[Save Rosbag]
    end

    E --> G
    E --> I

    style A fill:#d4e6f1,color:#000,stroke:#2874a6,stroke-width:2px
    style B fill:#d4e6f1,color:#000,stroke:#2874a6,stroke-width:2px
    style C fill:#d4e6f1,color:#000,stroke:#2874a6,stroke-width:2px
    style D fill:#f5b7b1,color:#000,stroke:#c0392b,stroke-width:2px
    style E fill:#d5f4e6,color:#000,stroke:#27ae60,stroke-width:2px

    style F fill:#aed6f1,color:#000,stroke:#2874a6,stroke-width:2px
    style G fill:#fef9e7,color:#000,stroke:#f39c12,stroke-width:2px
    style H fill:#fef9e7,color:#000,stroke:#f39c12,stroke-width:2px

    style I fill:#e8daef,color:#000,stroke:#8e44ad,stroke-width:2px
    style J fill:#e8daef,color:#000,stroke:#8e44ad,stroke-width:2px
    style K fill:#e8daef,color:#000,stroke:#8e44ad,stroke-width:2px

    style L fill:#d6eaf8,color:#000,stroke:#3498db,stroke-width:2px
    style M fill:#d6eaf8,color:#000,stroke:#3498db,stroke-width:2px
    style N fill:#abebc6,color:#000,stroke:#27ae60,stroke-width:3px
```

### Simplified Flow Diagram

```mermaid
flowchart TD
    Start([ROS Time Machine Starts]) --> Config[Config Manager<br/>✓ Load YAML<br/>✓ Validate]

    Config --> Setup[Setup 4 Components]

    Setup --> BM[Buffer Manager<br/>• Circular buffers<br/>• Always recording]
    Setup --> TM[Trigger Monitor<br/>• Watch events<br/>• Evaluate conditions]
    Setup --> RC[Recorder<br/>• Thread pool<br/>• Rosbag writer]
    Setup --> SV[Services<br/>• Manual triggers<br/>• Status queries]

    BM --> Running{System Running}
    TM --> Running
    RC --> Running
    SV --> Running

    Running -->|Continuous| Buffer[Buffer all topics<br/>Keep last N seconds]
    Running -->|Wait for| Event[Event detected!]

    Event --> Dump[Dump buffered data<br/>🔄 Pre-event capture]
    Dump --> Record[Record live data<br/>📹 Post-event capture]
    Record --> Save[💾 Save rosbag]
    Save --> Running

    style Start fill:#5dade2,color:#000,stroke:#2874a6,stroke-width:3px
    style Config fill:#58d68d,color:#000,stroke:#27ae60,stroke-width:2px
    style Setup fill:#f8b739,color:#000,stroke:#d68910,stroke-width:2px

    style BM fill:#aed6f1,color:#000,stroke:#2874a6,stroke-width:2px
    style TM fill:#d7bde2,color:#000,stroke:#8e44ad,stroke-width:2px
    style RC fill:#f9e79f,color:#000,stroke:#d4ac0d,stroke-width:2px
    style SV fill:#abebc6,color:#000,stroke:#27ae60,stroke-width:2px

    style Running fill:#fff,color:#000,stroke:#666,stroke-width:2px,stroke-dasharray:5
    style Buffer fill:#fef9e7,color:#000,stroke:#f39c12,stroke-width:2px
    style Event fill:#fadbd8,color:#000,stroke:#cb4335,stroke-width:3px
    style Dump fill:#d6eaf8,color:#000,stroke:#3498db,stroke-width:2px
    style Record fill:#d5f4e6,color:#000,stroke:#27ae60,stroke-width:2px
    style Save fill:#abebc6,color:#000,stroke:#27ae60,stroke-width:3px
```

### Component Interaction Sequence

```mermaid
sequenceDiagram
    participant User
    participant TriggerMonitor
    participant BufferManager
    participant Recorder
    participant WorkerThread
    participant RosBag2
    participant Topics as ROS Topics

    Note over BufferManager,Topics: Continuous buffering (always running)
    Topics->>BufferManager: /camera/image_raw
    Topics->>BufferManager: /lidar/points
    Topics->>BufferManager: /odom
    BufferManager->>BufferManager: Store in circular buffers

    Note over TriggerMonitor,Topics: Monitoring for events
    Topics->>TriggerMonitor: /emergency_stop: true
    TriggerMonitor->>TriggerMonitor: Evaluate condition<br/>(data == true)
    TriggerMonitor->>TriggerMonitor: Check rate limit

    TriggerMonitor->>Recorder: Trigger Event<br/>(snapshot: "emergency_stop")

    Recorder->>WorkerThread: Assign recording job
    activate WorkerThread

    WorkerThread->>BufferManager: Get buffered data<br/>(topics: [/camera, /lidar, /odom]<br/>duration: 5.0s)
    BufferManager-->>WorkerThread: Return messages<br/>(t-5.0 to t0)

    WorkerThread->>RosBag2: Create rosbag<br/>("/tmp/emergency_stop_1234.mcap")
    WorkerThread->>RosBag2: Write buffered messages

    Note over WorkerThread,Topics: Continue recording for post-event duration
    WorkerThread->>Topics: Subscribe temporarily
    Topics->>WorkerThread: Live messages (t0 to t+10.0)
    WorkerThread->>RosBag2: Write live messages

    Note over WorkerThread: Post-event duration elapsed
    WorkerThread->>Topics: Unsubscribe
    WorkerThread->>RosBag2: Close rosbag
    WorkerThread->>Recorder: Recording complete
    deactivate WorkerThread

    Recorder->>User: Publish RecordingStatus<br/>(COMPLETED)
```

### Recording Lifecycle State Machine

```mermaid
stateDiagram-v2
    [*] --> Idle: System Start

    Idle --> Triggered: Event condition met<br/>or Manual trigger

    Triggered --> RateLimited: Rate limit exceeded
    RateLimited --> Idle: Wait & retry

    Triggered --> DumpingBuffer: Rate limit OK

    DumpingBuffer --> Recording: Buffer dumped successfully
    DumpingBuffer --> Failed: Buffer dump error

    Recording --> Writing: Recording post-event data
    Writing --> Completing: Duration elapsed

    Completing --> Completed: Rosbag saved successfully
    Completing --> Failed: Write error

    Completed --> Idle: Ready for next trigger
    Failed --> Idle: Error logged

    note right of DumpingBuffer
        Retrieve N seconds of
        buffered data from
        circular buffers
    end note

    note right of Recording
        Subscribe to topics
        Record M seconds
        of live data
    end note

    note right of Completing
        Close rosbag
        Publish status
        Release resources
    end note
```

### Buffer Management Strategy

```mermaid
flowchart LR
    subgraph Buffers["Circular Buffers (Always Recording)"]
        direction TB
        B1["📷 /camera
        [msg1...msg100]
        Last 5 seconds"]

        B2["🎯 /lidar
        [msg1...msg50]
        Last 5 seconds"]

        B3["🧭 /odom
        [msg1...msg200]
        Last 5 seconds"]
    end

    Topics[ROS Topics<br/>Publishing] -->|Continuous| Buffers

    Trigger[⚡ Trigger Event<br/>t = 0] -->|Request| Dump[Dump Buffers<br/>t-5s to t0]

    Buffers -.->|Copy data| Dump

    Dump --> Bag[💾 Rosbag File<br/>emergency_stop.mcap]
    Live[📡 Live Recording<br/>t0 to t+10s] --> Bag

    style Topics fill:#aed6f1,color:#000,stroke:#2874a6,stroke-width:2px
    style Buffers fill:#fef9e7,color:#000,stroke:#f39c12,stroke-width:2px
    style B1 fill:#fff,color:#000,stroke:#f39c12,stroke-width:1px
    style B2 fill:#fff,color:#000,stroke:#f39c12,stroke-width:1px
    style B3 fill:#fff,color:#000,stroke:#f39c12,stroke-width:1px
    style Trigger fill:#fadbd8,color:#000,stroke:#cb4335,stroke-width:3px
    style Dump fill:#d6eaf8,color:#000,stroke:#3498db,stroke-width:2px
    style Live fill:#d5f4e6,color:#000,stroke:#27ae60,stroke-width:2px
    style Bag fill:#abebc6,color:#000,stroke:#27ae60,stroke-width:3px
```

**Key Concept:** One buffer per topic, shared by all snapshots. When triggered, dump only the topics needed for that specific snapshot.

## Core Components

### 1. Config Manager

**Responsibility:** Parse and validate YAML configuration files

**Features:**
- YAML configuration parsing
- Snapshot configuration validation
- Runtime configuration access

**Example Configuration:**
```yaml
snapshots:
  - name: "collision_event"
    trigger:
      topic: "/emergency_stop"
      msg_type: "std_msgs/msg/Bool"
      condition:
        field: "data"
        operator: "=="
        value: 1.0
    recording:
      topics:
        - "/camera/image_raw"
        - "/lidar/points"
        - "/odom"
      pre_event_duration: 5.0  # seconds
      post_event_duration: 10.0  # seconds
      max_buffer_size: 1000  # MB
    rate_limit:
      max_triggers_per_minute: 5
    output:
      path: "/data/snapshots"
      name_pattern: "{snapshot_name}_{timestamp}"
      format: "mcap"
```

### 2. Buffer Manager

**Responsibility:** Maintain circular buffers for configured topics

**Features:**
- Thread-safe circular buffer per topic
- Dynamic topic discovery
- Configurable buffer size based on duration
- Memory-efficient message storage
- Buffer statistics tracking

**Design Decisions:**
- Separate buffer per topic (not per snapshot) to avoid duplication
- Lock-based thread safety for concurrent access
- Drop oldest messages on overflow

### 3. Trigger Monitor

**Responsibility:** Monitor topics and evaluate trigger conditions

**Features:**
- Dynamic topic subscription
- Condition evaluation engine (==, >, <, >=, <=, !=)
- Support for nested message fields
- Rate limiting per snapshot
- Trigger event signaling

### 4. Recorder

**Responsibility:** Coordinate recording on trigger events

**Features:**
- Multi-threaded recording with worker pool
- Buffer dump coordination
- Post-event live recording
- MCAP rosbag2 format output
- Multiple simultaneous recordings support

**Threading Strategy:**
- Main thread: Listen for trigger events
- Worker thread pool: Handle individual recordings
- Each recording job:
  1. Dump buffer to rosbag
  2. Subscribe to topics for post-event duration
  3. Write to rosbag2 file
  4. Cleanup and publish status

### 5. Service Interface

**Responsibility:** Runtime control and monitoring

**Services:**
- Manual snapshot triggering
- System status queries
- Configuration listing

**Topics:**
- Recording status events
- Buffer statistics

## Component Composition

```
┌─────────────────────────────────────────────────────────┐
│                   ROS Time Machine Node                  │
├─────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │   Config     │  │   Trigger    │  │   Buffer     │  │
│  │   Manager    │──│   Monitor    │──│   Manager    │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │
│         │                  │                  │          │
│         └──────────────────┴──────────────────┘          │
│                            │                             │
│                   ┌──────────────┐                       │
│                   │   Recorder   │                       │
│                   │   Component  │                       │
│                   └──────────────┘                       │
│                            │                             │
│                   ┌──────────────┐                       │
│                   │   Service    │                       │
│                   │   Interface  │                       │
│                   └──────────────┘                       │
└─────────────────────────────────────────────────────────┘
```

## Technology Stack

### ROS2 Dependencies
- **ROS2 Distribution:** Humble Hawksbill (LTS) or Jazzy Jalisco
- **Build System:** colcon with ament_cmake
- **Language:** C++ (core components)
- **Dependencies:**
  - rclcpp (ROS2 C++ client library)
  - rosbag2_cpp (recording)
  - std_msgs, sensor_msgs (message types)
  - yaml-cpp (configuration parsing)

### Testing
- GTest for C++ unit tests
- launch_testing for ROS2 integration tests

## Performance Considerations

### Memory Management
- Buffer sizes calculated from topic bandwidth and pre-event duration
- Example: 10Hz image topic @ 1MB/msg, 5s buffer = 50MB
- Configurable memory limits with graceful degradation

### Threading Strategy
- Buffer updates: Per-topic mutex locks
- Recording threads: Configurable worker pool (default: 4)
- Non-blocking trigger handling
- Async I/O for disk writes

### I/O Optimization
- Thread pool sized for available I/O capacity
- Buffered writes to minimize syscalls
- MCAP format for efficient storage
- Queue management to prevent memory overflow
