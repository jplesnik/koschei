# Copyright (C) 2014  Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Author: Michael Simacek <msimacek@redhat.com>

import os
import shutil
import librepo
from common import DBTest, testdir, postgres_only
from mock import Mock, patch
from koschei.models import (Dependency, ResolutionResult, ResolutionProblem,
                            DependencyChange, Package)
from koschei.resolver import Resolver

FOO_DEPS = [
    ('A', 0, '1', '1.fc22', 'x86_64'),
    ('B', 0, '4.1', '2.fc22', 'noarch'),
    ('C', 1, '3', '1.fc22', 'x86_64'),
    ('D', 2, '8.b', '1.rc1.fc22', 'x86_64'),
    ('E', 0, '0.1', '1.fc22.1', 'noarch'),
    ('F', 0, '1', '1.fc22', 'noarch'),
    ('R', 0, '3.3', '2.fc22', 'x86_64'), # in build group
]

def get_repo(name):
    # hawkey sacks cannot be easily populated from within python and mocking
    # hawkey queries would be too complicated, therefore using real repos
    h = librepo.Handle()
    h.local = True
    h.repotype = librepo.LR_YUMREPO
    h.urls = [os.path.join('repo', name)]
    h.yumdlist = ['primary', 'filelists', 'group']
    return h.perform(librepo.Result())

class ResolverTest(DBTest):
    def __init__(self, *args, **kwargs):
        super(ResolverTest, self).__init__(*args, **kwargs)
        self.repo_mock = None
        self.srpm_mock = None

    def setUp(self):
        super(ResolverTest, self).setUp()
        shutil.copytree(os.path.join(testdir, 'test_repo'), 'repo')
        self.repo_mock = Mock()
        self.repo_mock.get_repos.return_value = {'x86_64': get_repo('x86_64')}
        self.srpm_mock = Mock()
        self.srpm_mock.get_repodata.return_value = get_repo('src')
        self.resolver = Resolver(db=self.s, koji_session=Mock(),
                        repo_cache=self.repo_mock, srpm_cache=self.srpm_mock)

    def prepare_foo_build(self, repo_id=666, version='4'):
        self.prepare_packages(['foo'])
        foo_build = self.prepare_builds(foo=True, repo_id=None)[0]
        foo_build.repo_id = repo_id
        foo_build.version = version
        foo_build.release = '1.fc22'
        self.s.commit()
        return foo_build

    def test_resolve_build(self):
        foo_build = self.prepare_foo_build()
        package_id = foo_build.package_id
        with patch('koschei.util.get_build_group', return_value=['R']):
            self.resolver.process_builds()
        self.repo_mock.get_repos.assert_called_once_with(666)
        self.srpm_mock.get_srpm.assert_called_once_with('foo', None, '4', '1.fc22')
        self.srpm_mock.get_repodata.assert_called_once_with()
        expected_result = [(package_id, 666, True)]
        actual_result = self.s.query(ResolutionResult.package_id,
                                     ResolutionResult.repo_id,
                                     ResolutionResult.resolved).all()
        self.assertItemsEqual(expected_result, actual_result)
        expected_deps = [tuple([package_id, 666] + list(nevr)) for nevr in FOO_DEPS]
        actual_deps = self.s.query(Dependency.package_id, Dependency.repo_id,
                                   Dependency.name, Dependency.epoch,
                                   Dependency.version, Dependency.release,
                                   Dependency.arch).all()
        self.assertItemsEqual(expected_deps, actual_deps)

    def test_resolution_fail(self):
        self.prepare_packages(['foo', 'bar'])
        bar = self.prepare_builds(bar=True, repo_id=None)[0]
        bar.repo_id = 666
        bar.epoch = 1
        bar.version = '2'
        bar.release = '2'
        self.s.commit()
        with patch('koschei.util.get_build_group', return_value=['R']):
            self.resolver.process_builds()
        self.repo_mock.get_repos.assert_called_once_with(666)
        self.srpm_mock.get_srpm.assert_called_once_with('bar', 1, '2', '2')
        self.srpm_mock.get_repodata.assert_called_once_with()
        expected_result = [(bar.package_id, 666, False)]
        actual_result = self.s.query(ResolutionResult.package_id,
                                     ResolutionResult.repo_id,
                                     ResolutionResult.resolved).all()
        self.assertItemsEqual(expected_result, actual_result)
        self.assertTrue(self.s.query(ResolutionProblem).count())

    def prepare_old_build(self):
        old_build = self.prepare_foo_build(repo_id=555, version='3')
        old_build.deps_processed = True
        old_deps = FOO_DEPS[:]
        old_deps[2] = ('C', 1, '2', '1.fc22', 'x86_64')
        del old_deps[4] # E
        package_id = old_build.package_id
        for n, e, v, r, a in old_deps:
            self.s.add(Dependency(package_id=package_id, repo_id=555, arch=a,
                                  name=n, epoch=e, version=v, release=r))
        self.s.add(ResolutionResult(package_id=package_id, repo_id=555, resolved=True))
        self.s.commit()
        return old_build

    def verify_changes(self):
        package_id = self.s.query(Package.id).filter_by(name='foo').scalar()
        expected_changes = [(package_id, 'C', 1, 1, '2', '3', '1.fc22', '1.fc22', 2),
                            (package_id, 'E', None, 0, None, '0.1', None, '1.fc22.1', 2)]
        c = DependencyChange
        actual_changes = self.s.query(c.package_id, c.dep_name, c.prev_epoch,
                                      c.curr_epoch, c.prev_version, c.curr_version,
                                      c.prev_release, c.curr_release, c.distance).all()
        self.assertItemsEqual(expected_changes, actual_changes)

    def test_differences(self):
        self.prepare_old_build()
        self.prepare_foo_build(repo_id=666, version='4')
        with patch('koschei.util.get_build_group', return_value=['R']):
            self.resolver.process_builds()
        self.verify_changes()

    @postgres_only
    def test_repo_generation(self):
        self.prepare_old_build()
        with patch('koschei.util.get_build_group', return_value=['R']):
            self.resolver.generate_repo(666)
        self.repo_mock.get_repos.assert_called_once_with(666)
        self.srpm_mock.get_latest_srpms.assert_called_once_with(['foo'])
        self.srpm_mock.get_repodata.assert_called_once_with()
        self.verify_changes()
