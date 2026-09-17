"""DEP-07 / T1.05：systemd_units 已停用为 no-op（唯一权威源 = deploy/systemd/）。

原用例断言的是 FHS 迁移前的旧布局 unit 文本（/opt/ttbox/core/current/ttbox-core、
TTBOX_CONFIG_PATH=/opt/ttbox/config/current 等），与 deploy/systemd/ 权威 unit 冲突。
现改为锁定"已停用"这一事实，防止有人重新引入第二套 unit 文本。
"""
import unittest

from platform.supervisor import systemd_units


class DeprecatedUnitTests(unittest.TestCase):
    def test_constants_are_noop(self):
        # no-op：不再产出任何 unit 文本
        self.assertEqual(systemd_units.CORE_UNIT, "")
        self.assertEqual(systemd_units.SUPERVISOR_UNIT, "")

    def test_module_documents_authority_source(self):
        doc = systemd_units.__doc__ or ""
        self.assertIn("废弃", doc)
        self.assertIn("deploy/systemd/", doc)


if __name__ == '__main__':
    unittest.main()
