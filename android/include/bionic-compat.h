#ifndef __BIONIC_COMPAT_H
#define __BIONIC_COMPAT_H

/* These functions and definitions aren't candidates for adding to bionic:
 * they've either been removed from POSIX or are glibc extensions.
 */

#define _GNU_SOURCE
#include <limits.h>
#include <linux/icmp.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/sysmacros.h>
#include <sys/time.h>
#include <sys/types.h>
#include <sys/utsname.h>
#include <unistd.h>

#define __GLIBC_PREREQ(a, b) 1

// TODO: remove after https://android-review.git.corp.google.com/c/platform/bionic/+/3034234
#define NGROUPS NGROUPS_MAX
// TODO: remove after https://android-review.git.corp.google.com/c/platform/bionic/+/3034152
#define LINE_MAX _POSIX2_LINE_MAX
// TODO: remove after https://android-review.git.corp.google.com/c/platform/bionic/+/3034233
#define SHMLBA sysconf(_SC_PAGESIZE)

#define _UTSNAME_DOMAIN_LENGTH SYS_NMLN
#define _UTSNAME_LENGTH SYS_NMLN

/* Bionic doesn't support extended patterns and
 * as of LTP20180515, this only used by read_all.c
 * which is run from runtest/fs.
 *
 * LTP does not pass the argument that will end up
 * going down the extended pattern match call, so
 * we are safe here. This is for *build* purposes only
 * and we don't alter behavior
 */
#define FNM_EXTMATCH 0

typedef unsigned long ulong;

static inline void *valloc(size_t size)
{
    return memalign(sysconf(_SC_PAGESIZE), size);
}

static inline char *get_current_dir_name(void)
{
    return getcwd(NULL, 0);
}

static inline int getdtablesize(void)
{
    struct rlimit lim;
    int err = getrlimit(RLIMIT_NOFILE, &lim);
    if (err < 0)
        return err;

    return lim.rlim_cur;
}

static inline void pthread_testcancel(void) { }
static inline int pthread_cancel(pthread_t thread) { return 0; }

#endif /* __BIONIC_COMPAT_H */
