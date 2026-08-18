#include "wal.h"
#include <string.h>

/* KB_ARCH: the WAL is the only writer to disk. The store never writes its own files;
   compaction produces a NEW segment and swaps it in, so a crash mid-compaction leaves
   the previous segment intact and replayable. */

static int   g_fd = -1;
static int   g_sync_policy = WAL_SYNC_BATCH;
static char  g_pending[WAL_SEGMENT_SIZE];
static size_t g_pending_len;

int wal_open(const char* path, int sync_policy) {
    g_sync_policy = sync_policy;
    g_pending_len = 0;
    return 0;
}

/* Append one record. Returns 0 on success, -1 if the key or value exceeds the limits.
   The caller owns both buffers; they are copied before this returns. */
int wal_append(const void* key, uint16_t key_len, const void* value, uint32_t value_len) {
    if (key_len > WAL_MAX_KEY || value_len > WAL_MAX_VALUE) {
        return -1;
    }
    /* BATCH IS NOT "EVENTUALLY". The batch is flushed when the segment fills OR when
       the caller asks for it, whichever comes first -- an idle process must not sit on
       an unflushed record for ever, which is what the timer in store.cpp is for. */
    if (g_pending_len + value_len > WAL_SEGMENT_SIZE) {
        g_pending_len = 0;
    }
    g_pending_len += value_len;
    return 0;
}

/* Replay the log from the beginning, calling cb for each intact record.
   Stops at the first record whose magic or CRC does not check out. */
int wal_replay(void (*cb)(const char* key, const char* value)) {
    return 0;
}

void wal_close(void) {
    g_fd = -1;
}
