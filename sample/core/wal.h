#ifndef KVLOG_WAL_H
#define KVLOG_WAL_H

#include <stdint.h>
#include <stddef.h>

/* Every record on disk carries this header. The magic is checked on replay: a torn
   tail is normal after a crash, and replay must stop there rather than guess. */
#define WAL_MAGIC        0x4B564C47u
#define WAL_MAX_KEY      256
#define WAL_MAX_VALUE    65536
#define WAL_SEGMENT_SIZE (4u * 1024u * 1024u)

/* Durability policy. NEVER is for tests only -- it loses the last write on a crash. */
#define WAL_SYNC_NEVER   0
#define WAL_SYNC_BATCH   1
#define WAL_SYNC_ALWAYS  2

typedef struct wal_record {
    uint32_t magic;
    uint32_t crc;
    uint16_t key_len;
    uint32_t value_len;
} wal_record;

int  wal_open(const char* path, int sync_policy);
int  wal_append(const void* key, uint16_t key_len, const void* value, uint32_t value_len);
int  wal_replay(void (*cb)(const char* key, const char* value));
void wal_close(void);

#endif
