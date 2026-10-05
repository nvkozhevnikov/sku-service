import unittest
from universal_supplier.kami_robots import KamiRobots


class KamiRobotsTests(unittest.TestCase):
    def setUp(self):
        self.robot = KamiRobots('''User-agent: *
Disallow: /*?
Disallow: /local/
Disallow: /*.pdf
Disallow: /*PAGEN_
Allow: /catalog/*?PAGEN_*=*
Disallow: /*?PAGEN_1=1$
Disallow: /catalog/*?*PAGEN_*=*&*
User-agent: Googlebot
Disallow: /
''')

    def test_wildcard_deny(self):
        for path in ('/catalog/a/?filter=x', '/upload/guide.pdf', '/local/ajax.php', '/x?PAGEN_1=2'):
            self.assertFalse(self.robot.can_fetch('https://www.stanki.ru' + path), path)

    def test_longest_allow_and_trailing_query_deny(self):
        self.assertTrue(self.robot.can_fetch('https://www.stanki.ru/catalog/a/?PAGEN_1=2'))
        self.assertFalse(self.robot.can_fetch('https://www.stanki.ru/catalog/a/?PAGEN_1=2&sort=x'))

    def test_named_other_bot_does_not_apply(self):
        self.assertTrue(self.robot.can_fetch('https://www.stanki.ru/catalog/a/item/'))
        self.assertFalse(KamiRobots('User-agent: Googlebot\nDisallow: /', 'Googlebot').can_fetch('https://x/'))

    def test_unknown_groups_fail_closed(self):
        with self.assertRaises(ValueError):
            KamiRobots('User-agent: Otherbot\nAllow: /')
