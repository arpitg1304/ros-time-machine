# Recording status message
# Published to indicate the status of ongoing recordings

# Status constants
uint8 STATUS_IDLE = 0
uint8 STATUS_TRIGGERED = 1
uint8 STATUS_RECORDING = 2
uint8 STATUS_COMPLETED = 3
uint8 STATUS_FAILED = 4

# Current status
uint8 status

# Snapshot name
string snapshot_name

# Output file path (when recording or completed)
string output_path

# Timestamp when recording was triggered
builtin_interfaces/Time trigger_time

# Number of topics being recorded
uint32 num_topics

# Estimated buffer size dumped (bytes)
uint64 buffer_size_bytes

# Error message (if status is FAILED)
string error_message
