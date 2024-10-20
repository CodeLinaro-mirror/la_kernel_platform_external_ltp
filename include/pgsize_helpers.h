// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Copyright (c) 2024 Google LLC. All rights reserved.
 * Author(s): Kalesh Singh <kaleshsingh@google.com>
 */

#ifndef PGSIZE_HELPER_H
#define PGSIZE_HELPER_H

#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define MAX_PAGE_SIZE (64*1024)

#if defined(__x86_64__)
/*
 * Android emulates the userspace page size on some x86_64 emulators.
 * The kernel page size still remains 4KiB (0x1000).
 */
static inline size_t kernel_page_size(void)
{
    return 0x1000;
}

/* Handle upto MAX_PAGE_SIZE emulation on 4KiB kernel base page size system */
#define DECLARE_MINCORE_VECTOR(vec_name, num_pages) \
    unsigned char vec_name[(num_pages) * (MAX_PAGE_SIZE / 4096)]

#else	/* !defined(__x86_64__) */

static inline size_t kernel_page_size(void)
{
	return getpagesize();
}

#define DECLARE_MINCORE_VECTOR(vec_name, num_pages) unsigned char vec_name[(num_pages)]
#endif  /* defined(__x86_64__) */

/*
 * NOTE: For all cases except Android x86_64 page size emulators,
 * kernel_page_size == page_size, and the below macros are effectively
 * no-ops.
 */

/* The number of kernel pages covered by size */
static inline size_t nr_kernel_pages(size_t size)
{
    return size / kernel_page_size();
}

/* The number of kernel pages in a @nr_pages userspace pages */
static size_t nr_pgs_to_nr_kernel_pgs(size_t nr_pages)
{
    return nr_pages * nr_kernel_pages(getpagesize());
}

/* The number of userspace pages in a @nr_pages kernel pages */
static size_t nr_kernel_pgs_to_nr_pgs(size_t nr_pages)
{
    return nr_pages / nr_kernel_pages(getpagesize());
}

/*
 * Test populating the last partial 4KiB page if the page size is emulated,
 * instead of the first. This faults in the preceding pages for convenient
 * test accounting.
 */
#define MLOCK_PAGE_SIZE_EMULATION_OFFSET(tcases)                                            \
do {                                                                                        \
    if (getpagesize() != kernel_page_size()) {                                              \
        for (int i = 0; i < ARRAY_SIZE(tcases); i++) {                                      \
            struct tcase *test = tcases + i;                                                \
            if (test->offset > 0) test->offset = pgsz - kernel_page_size() + test->offset;  \
        }                                                                                   \
    }                                                                                       \
} while (0)

/*
 * Make the backing file large enough to cover the last corresponding kernel page.
 *
 * This is an artifact of x86_64 page size emulation on Android, to handle
 * file_map_fault's, which does allow access to the partial page after the end
 * of the file.
 */
#define SAFE_FILE_PRINTF_PGSIZE_EMULATION(file, str)                            \
do {                                                                            \
    if (kernel_page_size() == getpagesize()) {                                  \
        SAFE_FILE_PRINTF(file, str);                                            \
    } else {                                                                    \
        int str_len = strlen(str);                                              \
        int nr_writes = ((kernel_page_size()                                    \
                            * (nr_pgs_to_nr_kernel_pgs(1) - 1)) / str_len) + 1; \
        int total_len = str_len * nr_writes;                                    \
        char *buffer = SAFE_MALLOC(total_len + 1);                              \
        for (int i = 0; i < nr_writes; i++)                                     \
            strcat(buffer, str);                                                \
        SAFE_FILE_PRINTF(file, buffer);                                         \
        free(buffer);                                                           \
    }                                                                           \
} while (0)
#endif /* PGSIZE_HELPER_H */
