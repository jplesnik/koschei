import os
import librepo
import contextlib

from mock import PropertyMock, Mock, patch, call

from common import AbstractTest

from koschei import repo_cache

class MockRepo(object):
    pass

@contextlib.contextmanager
def librepo_mock():
    mock = Mock()
    with patch('librepo.Handle', return_value=mock):
        mock.perform.return_value = MockRepo
        for prop in 'destdir', 'repotype', 'urls', 'yumdlist', 'local':
            prop_mock = PropertyMock()
            setattr(type(mock), prop, prop_mock)
            setattr(mock, 'mock_' + prop, prop_mock)
        yield mock

arches = ['x86_64', 'i386']

def repoids(repos):
    for repo in repos:
        for arch in arches:
            yield repo, arch

def repodirs(repos):
    for repo, arch in repoids(repos):
        yield os.path.join('.', str(repo), arch)

def repourls(repos):
    for repo, arch in repoids(repos):
        yield 'http://example.com/{repo}/{arch}'.format(repo=repo, arch=arch)

class RepoCacheTest(AbstractTest):
    def setUp(self):
        super(RepoCacheTest, self).setUp()
        repos = [7, 123, 666, 1024]
        for repo in repodirs(repos):
            os.makedirs(repo)
        os.mkdir('not-repo')

    def test_read_from_disk(self):
        with librepo_mock() as mock:
            repo_cache.RepoCache()
            repos = 7, 123, 666, 1024
            mock.mock_local.assert_has_calls([call(True)] * 4)
            mock.mock_repotype.assert_has_calls([call(librepo.LR_YUMREPO)] * 4)
            mock.mock_yumdlist.assert_has_calls([call(['primary', 'filelists', 'group'])] * 4)
            mock.mock_urls.assert_has_calls([call([p]) for p in repodirs(repos)])
            self.assertEqual(8, mock.perform.call_count)

    def test_lru_init(self):
        with librepo_mock():
            repo_cache.RepoCache()
            self.assertEqual({'123', '666', '1024', 'not-repo'}, set(os.listdir('.')))

    def test_get_cached(self):
        with librepo_mock() as mock:
            cache = repo_cache.RepoCache()
            mock.reset_mock()
            self.assertEqual({'x86_64': MockRepo, 'i386': MockRepo},
                             cache.get_repos(666))
            self.assertFalse(mock.perform.called)

    def test_download(self):
        with librepo_mock() as mock:
            cache = repo_cache.RepoCache()
            mock.reset_mock()
            self.assertEqual(MockRepo, cache.get_repo(2000, 'i386'))
            mock.mock_repotype.assert_has_calls([call(librepo.LR_YUMREPO)] * 2)
            mock.mock_yumdlist.assert_has_calls([call(['primary', 'filelists', 'group'])] * 2)
            mock.mock_urls.assert_has_calls([call([p]) for p in repourls([2000])])
            mock.mock_destdir.assert_has_calls([call(p) for p in repodirs([2000])])
            self.assertEqual({'2000', '666', '1024', 'not-repo'}, set(os.listdir('.')))

    def test_lru_basic(self):
        with librepo_mock():
            cache = repo_cache.RepoCache()
            cache.get_repo(123, 'x86_64')
            cache.get_repo(2000, 'i386')
            self.assertEqual({'2000', '123', '1024', 'not-repo'}, set(os.listdir('.')))

    def test_lru_more(self):
        with librepo_mock():
            cache = repo_cache.RepoCache()
            for repo in 5555, 666, 1024, 2000, 123, 7:
                cache.get_repo(repo, 'x86_64')
            self.assertEqual({'2000', '123', '7', 'not-repo'}, set(os.listdir('.')))
