#! /usr/bin/env python3
#
# Copyright 2018 - The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tool for comparing 2 LTP projects to find added / removed tests & testsuites.

Each side can be a directory (--ltp-old / --ltp-new), a git revision of the
LTP repository that contains this script (--old-rev / --new-rev), or a json
file dumped by a previous run (--ltp-old-json / --ltp-new-json), i.e.:

$ compare_ltp_projects.py --old-rev goog/main --ltp-new .
$ compare_ltp_projects.py --old-rev 7cce28f86^1 --new-rev 7cce28f86

The tool can also be run in two separate steps by dumping the list of tests as
a json file, i.e.:

$ git checkout master
$ compare_ltp_projects.py --ltp-old . --ltp-old-json ./ltp_old.json
$ git checkout mymergebranch
$ compare_ltp_projects.py --ltp-old-json ./ltp_old.json --ltp-new .
"""

import argparse
import io
import json
import logging
import os
import os.path
import subprocess
import sys
import tarfile
import tempfile

# The LTP git repository that contains this script. Revisions given with
# --old-rev / --new-rev are resolved in this repository.
LTP_GIT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.realpath(__file__))))

def scan_tests(ltp_root, suite):
    ''' Find all tests that are run as part of given test suite in LTP.

    Args:
        ltp_root: Path ot the LTP project.
        suite: Name of testsuite.

    Returns:
        List of tests names that are run as part of the given test suite.
    '''

    tests = []
    if not suite:
        return tests

    runtest_dir = ltp_root + os.path.sep + 'runtest'
    test_suiteFile = runtest_dir + os.path.sep + suite
    if not os.path.isfile(test_suiteFile):
        logging.warning('No tests defined for suite %s', suite)
        return tests

    with open(test_suiteFile) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                tests.append(line.split()[0])
    f.close()
    return tests

def scan_test_suites(ltp_root, scenario):
    ''' Find all testuites and tests run as part of given LTP scenario

    Args:
        ltp_root: Path to the LTP project.
        scenario: name of the scenario (found in ltp_root/scenario_groups). E.g. "vts"

    Returns:
        List of testsuite names that are run as part of given scenario (e.g. vts).
        If scenario is not specified, return all testsuites.
    '''

    runtest_dir = ltp_root + os.path.sep + 'runtest'
    if not os.path.isdir(runtest_dir):
        logging.error('Invalid ltp_root %s, runtest directory doesnt exist', ltp_root)
        sys.exit(2)

    test_suites = []
    if scenario:
        scenarioFile = ltp_root + os.path.sep + 'scenario_groups' + os.path.sep + scenario
        if not os.path.isfile(scenarioFile):
            return test_suites
        with open(scenarioFile) as f:
            for line in f:
                line = line.strip()
                if not line.startswith('#'):
                    test_suites.append(line)
        return test_suites

    runtest_dir = ltp_root + os.path.sep + 'runtest'
    test_suites = [f for f in os.listdir(runtest_dir) if os.path.isfile(runtest_dir + os.path.sep + f)]

    return test_suites

def scan_ltp(ltp_root, scenario):
    ''' scan LTP project and return all tests and testuites present.

    Args:
        ltp_root: Path to the LTP project.
        scenario: specific scenario we want to scan. e.g. vts

    Returns:
        Dictionary of all LTP test names keyed by testsuite they are run as part of.
        E.g.
             {
                 'mem': ['oom01', 'mem02',...],
                 'syscalls': ['open01', 'read02',...],
                 ...
             }
    '''

    if not os.path.isdir(ltp_root):
        logging.error('ltp_root %s does not exist', ltp_root)
        sys.exit(1)

    test_suites = scan_test_suites(ltp_root, scenario)
    if not test_suites:
        logging.error('No Testsuites found for scenario %s', scenario)
        sys.exit(3)

    ltp_tests = {}
    for suite in test_suites:
        ltp_tests[suite] = scan_tests(ltp_root, suite)
    return ltp_tests


def run_git(args):
    ''' Run a git command in LTP_GIT_ROOT.

    Args:
        args: list of string, arguments for git.

    Returns:
        subprocess.CompletedProcess with stdout and stderr captured as bytes.
    '''
    return subprocess.run(['git', '-C', LTP_GIT_ROOT] + args,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def resolve_rev(rev):
    ''' Resolve a git revision of the LTP project to a full commit SHA.

    Args:
        rev: string, anything git understands: SHA, tag, branch, HEAD~1, ...

    Returns:
        The full commit SHA. Exits with code 1 when LTP_GIT_ROOT is not a git
        repository or when rev does not name a commit.
    '''
    if rev.startswith('-'):
        logging.error("Invalid revision '%s'", rev)
        sys.exit(1)

    result = run_git(['rev-parse', '--verify', '--quiet', rev + '^{commit}'])
    if result.returncode != 0:
        logging.error("Revision '%s' not found in %s", rev, LTP_GIT_ROOT)
        error = result.stderr.decode(errors='replace').strip()
        if error:
            logging.error('%s', error)
        if os.path.isdir(rev):
            logging.error("'%s' is a directory, use --ltp-old / --ltp-new "
                          "for directories", rev)
        sys.exit(1)
    return result.stdout.decode().strip()


def scan_ltp_rev(rev, scenario):
    ''' Scan the runtest directory of a git revision of the LTP project.

    runtest/ (and scenario_groups/, when the revision has it) is extracted to
    a temporary directory and scanned with scan_ltp(). The temporary directory
    is removed before returning, also when the scan fails.

    Args:
        rev: string, git revision to scan, see resolve_rev().
        scenario: specific scenario we want to scan. e.g. vts

    Returns:
        Same as scan_ltp().
    '''
    sha = resolve_rev(rev)
    logging.info("'%s' resolved to %s", rev, sha)

    if run_git(['cat-file', '-e', sha + ':runtest']).returncode != 0:
        logging.error("Revision '%s' has no runtest directory", rev)
        sys.exit(2)

    paths = ['runtest']
    if run_git(['cat-file', '-e', sha + ':scenario_groups']).returncode == 0:
        paths.append('scenario_groups')

    result = run_git(['archive', '--format=tar', sha, '--'] + paths)
    if result.returncode != 0:
        logging.error("git archive failed for revision '%s'", rev)
        logging.error('%s', result.stderr.decode(errors='replace').strip())
        sys.exit(1)

    with tempfile.TemporaryDirectory(prefix='compare_ltp_') as tmp_root:
        with tarfile.open(fileobj=io.BytesIO(result.stdout)) as tar:
            if hasattr(tarfile, 'data_filter'):
                tar.extractall(tmp_root, filter='data')
            else:
                tar.extractall(tmp_root)
        return scan_ltp(tmp_root, scenario)

def show_diff(ltp_tests_1, ltp_tests_2):
    ''' Find and print diff between testcases in 2 LTP project checkouts.

    Args:
        ltp_tests_1: dictionary of tests keyed by test suite names
        ltp_tests_2: dictionary of tests keyed by test suite names
    '''
    test_suites1 = set(ltp_tests_1.keys())
    test_suites2 = set(ltp_tests_2.keys())

    # Generate lists of deleted, added and common test suites between
    # LTP1 & LTP2
    deleted_test_suites = sorted(test_suites1.difference(test_suites2))
    added_test_suites = sorted(test_suites2.difference(test_suites1))
    common_test_suites = test_suites1.intersection(test_suites2)

    # Tests that belong to a test suite which only exists in one of the two
    # projects. They are listed separately from the tests of the common test
    # suites, because enabling a new test suite is a separate decision from
    # enabling new tests of an already enabled test suite.
    deleted_suite_tests = sorted(set(
        suite + '.' + e for suite in deleted_test_suites for e in ltp_tests_1[suite]))
    added_suite_tests = sorted(set(
        suite + '.' + e for suite in added_test_suites for e in ltp_tests_2[suite]))

    deleted_tests = []
    added_tests = []
    for suite in common_test_suites:
        tests1 = set(ltp_tests_1[suite])
        tests2 = set(ltp_tests_2[suite])

        exclusive_test1 = tests1.difference(tests2)
        exclusive_test2 = tests2.difference(tests1)
        for e in exclusive_test1:
            deleted_tests.append(suite + '.' + e)
        for e in exclusive_test2:
            added_tests.append(suite + '.' + e)
    deleted_tests = sorted(deleted_tests)
    added_tests = sorted(added_tests)
    print_columns(added_test_suites, deleted_test_suites, added_tests, deleted_tests,
                  added_suite_tests, deleted_suite_tests)


def print_section(title, added, deleted, width):
    ''' Print one two-column section: added entries on the left, deleted
    entries on the right. Nothing is printed when both lists are empty.

    Args:
        title: section title, printed centered in a line of '*'
        added: list of added names
        deleted: list of deleted names
        width: width of each column
    '''
    total_rows = max(len(added), len(deleted))
    if not total_rows:
        return

    print('{:*^{len}}'.format(title, len=min(width*2+2, 72)))
    print('{:<{len}} {:<{len}}'.format('Added ({})'.format(len(added)),
                                       'Deleted ({})'.format(len(deleted)), len=width))
    for i in range(total_rows):
        print('{:<{len}} {:<{len}}'.format(added[i] if i < len(added) else '',
                                           deleted[i] if i < len(deleted) else '', len=width))


def print_columns(added_test_suites, deleted_test_suites, added_tests, deleted_tests,
                  added_suite_tests=(), deleted_suite_tests=()):
    ''' Print the added / deleted test suites and tests as two-column tables.

    Args:
        added_test_suites: test suites only present in the new project
        deleted_test_suites: test suites only present in the old project
        added_tests: tests added to test suites present in both projects
        deleted_tests: tests deleted from test suites present in both projects
        added_suite_tests: tests of the added test suites
        deleted_suite_tests: tests of the deleted test suites
    '''
    sections = [
        (' Tests Suites ', added_test_suites, deleted_test_suites),
        # Printed right below the test suites they belong to, and separately
        # from the tests of the common test suites, because enabling a new
        # test suite is a separate decision from enabling new tests.
        (' Tests in Added/Deleted Suites ', added_suite_tests, deleted_suite_tests),
        (' Tests ', added_tests, deleted_tests),
    ]

    # One shared width for all sections, so their columns line up. It also
    # covers the header texts so the columns stay aligned even when all names
    # are shorter than 'Added (N)' / 'Deleted (N)'.
    width = 0
    for _, added, deleted in sections:
        width = max([width,
                     len('Added ({})'.format(len(added))),
                     len('Deleted ({})'.format(len(deleted)))] +
                    [len(x) for x in added] +
                    [len(x) for x in deleted])

    # Non-empty sections are separated by a single blank line.
    first = True
    for title, added, deleted in sections:
        if not added and not deleted:
            continue
        if not first:
            print('')
        print_section(title, added, deleted, width)
        first = False


def main():
    # All diagnostics (INFO/WARNING/ERROR) go to stderr; stdout carries only
    # the report, so it can be redirected or pasted as is.
    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format='%(levelname)s: %(message)s')

    arg_parser = argparse.ArgumentParser(
        description='Diff 2 LTP projects for supported test cases')
    old_group = arg_parser.add_mutually_exclusive_group()
    old_group.add_argument('-o', '--ltp-old',
                           dest='ltp_old',
                           help="LTP Root Directory before merge")
    old_group.add_argument('--old-rev',
                           dest='old_rev', metavar='REV',
                           help="git revision (SHA, tag, branch, HEAD~1, ...) "
                                "before merge, resolved in the LTP repository "
                                "that contains this script")
    arg_parser.add_argument('-oj', '--ltp-old-json',
                            dest='ltp_old_json',
                            default='ltp_old.json',
                            help="Old LTP parsed directory in json format")
    new_group = arg_parser.add_mutually_exclusive_group()
    new_group.add_argument('-n', '--ltp-new',
                           dest='ltp_new',
                           help="LTP Root Directory after merge")
    new_group.add_argument('--new-rev',
                           dest='new_rev', metavar='REV',
                           help="git revision after merge, see --old-rev")
    arg_parser.add_argument('-nj', '--ltp-new-json',
                            dest='ltp_new_json',
                            default='ltp_new.json',
                            help="New LTP parsed directory in json format")
    arg_parser.add_argument('--scenario', default=None,
                            dest='scenario',
                            help="LTP scenario to list tests for")
    args = arg_parser.parse_args()

    # Find tests in the "old" directory or revision, or read the json output
    # from a prior run
    if args.ltp_old:
        ltp_tests1 = scan_ltp(args.ltp_old, args.scenario)
    elif args.old_rev:
        ltp_tests1 = scan_ltp_rev(args.old_rev, args.scenario)
    elif args.ltp_old_json and os.path.isfile(args.ltp_old_json):
        with open(args.ltp_old_json) as f:
            ltp_tests1 = json.load(f)
    else:
        ltp_tests1 = None

    # Do the same for the "new" directory or revision
    if args.ltp_new:
        ltp_tests2 = scan_ltp(args.ltp_new, args.scenario)
    elif args.new_rev:
        ltp_tests2 = scan_ltp_rev(args.new_rev, args.scenario)
    elif args.ltp_new_json and os.path.isfile(args.ltp_new_json):
        with open(args.ltp_new_json) as f:
            ltp_tests2 = json.load(f)
    else:
        ltp_tests2 = None

    if ltp_tests1 and ltp_tests2:
        # Show the difference of the two directories if both are present
        show_diff(ltp_tests1, ltp_tests2)
    elif ltp_tests1:
        # If just the old directory was read, dump it as json
        with open(args.ltp_old_json, 'w') as f:
            json.dump(ltp_tests1, f)
    elif ltp_tests2:
        # If just the new directory was read, dump it as json
        with open(args.ltp_new_json, 'w') as f:
            json.dump(ltp_tests2, f)

if __name__ == '__main__':
    main()
