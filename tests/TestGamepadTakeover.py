import ast
import time
import unittest
from pathlib import Path

from src.task.AutoCombatTask import AutoCombatTask


def make_task(in_team=None, health_bar=True, found_glyphs=(), **overrides):
    """构造一个跳过 ok 运行时初始化的 AutoCombatTask 替身。

    ``ensure_keyboard_ui`` 只依赖少数可替换的方法, 用实例属性覆盖即可离线测试。
    """
    task = AutoCombatTask.__new__(AutoCombatTask)
    task._gamepad_takeover_retry_after = 0.0
    task._gamepad_takeover_fail_count = 0
    task.moves = []
    task.clicks = 0
    task.in_team = in_team or (lambda: (False, -1, 1))
    task.check_health_bar = lambda: health_bar
    task.find_one = lambda name, threshold=0.6: name if name in found_glyphs else None
    task.wait_until = lambda condition, **kwargs: condition()
    task.move = lambda x, y: task.moves.append((x, y))
    task.click = lambda *args, **kwargs: setattr(task, 'clicks', task.clicks + 1)
    task.width_of_screen = lambda percent: 1000
    task.height_of_screen = lambda percent: 500
    task.log_info = lambda *args, **kwargs: None
    task.log_warning = lambda *args, **kwargs: None
    for key, value in overrides.items():
        setattr(task, key, value)
    return task


class TestGamepadTakeover(unittest.TestCase):

    def test_keyboard_ui_needs_no_input(self):
        task = make_task(in_team=lambda: (True, 0, 3))

        self.assertTrue(task.ensure_keyboard_ui())
        self.assertEqual(task.moves, [])
        self.assertEqual(task.clicks, 0)

    def test_no_input_when_not_in_combat(self):
        task = make_task(health_bar=False)

        self.assertFalse(task.ensure_keyboard_ui())
        self.assertEqual(task.moves, [])
        self.assertEqual(task.clicks, 0)
        # 没有战斗时不算一次失败, 不应触发冷却
        self.assertEqual(task._gamepad_takeover_retry_after, 0.0)

    def test_keyboard_glyph_present_is_not_gamepad_ui(self):
        task = make_task(found_glyphs=('q',))

        self.assertFalse(task.ensure_keyboard_ui())
        self.assertEqual(task.moves, [])
        self.assertEqual(task.clicks, 0)

    def test_takeover_primes_once_then_recovers(self):
        team_results = iter([(False, -1, 1), (False, -1, 1), (True, 0, 3)])
        task = make_task(in_team=lambda: next(team_results))

        self.assertTrue(task.ensure_keyboard_ui())
        self.assertEqual(len(task.moves), 2)
        self.assertEqual(task.clicks, 0)
        self.assertEqual(task._gamepad_takeover_fail_count, 0)

    def test_takeover_timeout_records_failure(self):
        task = make_task(
            wait_until=lambda condition, **kwargs: time.sleep(0.01) or False,
        )

        self.assertFalse(task.ensure_keyboard_ui(time_out=0.02))
        self.assertGreater(task._gamepad_takeover_retry_after, time.time())
        self.assertEqual(task._gamepad_takeover_fail_count, 1)

    def test_cooldown_blocks_input(self):
        task = make_task()
        task._gamepad_takeover_retry_after = time.time() + 10

        self.assertFalse(task.ensure_keyboard_ui())
        self.assertEqual(task.moves, [])
        self.assertEqual(task.clicks, 0)

    def test_prime_escalates_to_click(self):
        task = make_task()

        task.prime_keyboard_input(1)
        self.assertEqual(len(task.moves), 2)
        self.assertEqual(task.clicks, 0)

        task.prime_keyboard_input(task.GAMEPAD_CLICK_FROM_ATTEMPT)
        self.assertEqual(task.clicks, 1)

    def test_repeated_failures_back_off(self):
        task = make_task(
            wait_until=lambda condition, **kwargs: time.sleep(0.01) or False,
        )
        task._gamepad_takeover_fail_count = task.GAMEPAD_TAKEOVER_MAX_FAILS - 1

        self.assertFalse(task.ensure_keyboard_ui(time_out=0.02))
        self.assertEqual(task._gamepad_takeover_fail_count, 0)
        self.assertGreater(task._gamepad_takeover_retry_after - time.time(),
                           task.GAMEPAD_TAKEOVER_FAIL_COOLDOWN)


class TestAutoCombatGamepadWiring(unittest.TestCase):

    def test_run_takes_over_gamepad_ui_before_combat_loop(self):
        module = ast.parse(Path('src/task/AutoCombatTask.py').read_text(encoding='utf-8'))
        task_class = next(node for node in module.body
                          if isinstance(node, ast.ClassDef) and node.name == 'AutoCombatTask')
        run = next(node for node in task_class.body
                   if isinstance(node, ast.FunctionDef) and node.name == 'run')

        takeover_calls = [node for node in ast.walk(run)
                          if isinstance(node, ast.Call)
                          and isinstance(node.func, ast.Attribute)
                          and node.func.attr == 'ensure_keyboard_ui']
        combat_loop = [node for node in ast.walk(run)
                       if isinstance(node, ast.While)
                       and 'in_combat' in ast.unparse(node.test)]

        self.assertTrue(takeover_calls, 'AutoCombatTask.run must call ensure_keyboard_ui')
        self.assertTrue(combat_loop, 'AutoCombatTask.run must keep the in_combat loop')
        self.assertLess(takeover_calls[0].lineno, combat_loop[0].lineno)


if __name__ == '__main__':
    unittest.main()
