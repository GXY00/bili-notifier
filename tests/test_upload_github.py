"""Exercise the upload script against local repositories, never GitHub."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GIT = shutil.which("git")
POWERSHELL = shutil.which("powershell.exe")
BRANCH = "codex/upload-test"


@unittest.skipUnless(os.name == "nt" and GIT and POWERSHELL, "Requires Windows PowerShell and Git")
class UploadGitHubTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="upload-github-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "项目 空格 & !"
        self.repo.mkdir()
        self.remote = self.root / "remote.git"
        self.env = os.environ.copy()
        # Keep the tests independent of personal identity, hooks and credentials.
        for key in list(self.env):
            if key.startswith("GIT_"):
                del self.env[key]
        self.env.update(
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_TERMINAL_PROMPT="0",
            GCM_INTERACTIVE="never",
        )
        self.git("init", "--bare", str(self.remote))
        self.git("init", "--initial-branch", BRANCH)
        self.git("config", "user.name", "Upload Test")
        self.git("config", "user.email", "upload-test@example.invalid")
        self.git("config", "core.autocrlf", "false")
        self.git("remote", "add", "origin", str(self.remote))
        for filename in ("upload_github.ps1", "upload_github.bat", ".gitignore"):
            shutil.copyfile(PROJECT_ROOT / filename, self.repo / filename)
        (self.repo / "tracked.txt").write_text("original", encoding="utf-8")
        (self.repo / "deleted.txt").write_text("delete me", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-m", "initial")

    def git(self, *args, check=True):
        result = subprocess.run(
            [GIT, *args], cwd=self.repo, env=self.env,
            capture_output=True, encoding="utf-8", errors="replace", timeout=30,
        )
        if check and result.returncode:
            self.fail(f"git {args} failed:\n{result.stdout}\n{result.stderr}")
        return result.stdout.strip() if check else result

    def upload(self, message="", launcher=False):
        if launcher:
            command = f'cmd.exe /d /s /c ""{self.repo / "upload_github.bat"}""'
        else:
            command = [POWERSHELL, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                       "-File", str(self.repo / "upload_github.ps1")]
        # Start outside the project to verify that the script locates itself.
        return subprocess.run(
            command, cwd=self.root, env=self.env, input=message + "\n\n",
            capture_output=True, encoding="utf-8", errors="replace", timeout=45,
        )

    def assert_uploaded(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        local_head = self.git("rev-parse", "HEAD")
        remote_head = self.git("--git-dir", str(self.remote), "rev-parse", f"refs/heads/{BRANCH}")
        self.assertEqual(local_head, remote_head)
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "@{upstream}"), f"origin/{BRANCH}")

    def test_custom_message_all_changes_and_ignored_files(self):
        (self.repo / "tracked.txt").write_text("staged", encoding="utf-8")
        self.git("add", "tracked.txt")
        (self.repo / "tracked.txt").write_text("latest", encoding="utf-8")
        (self.repo / "deleted.txt").unlink()
        (self.repo / "中文 新文件.txt").write_text("new", encoding="utf-8")
        for filename in ("config.json", ".env", "crash.log", "bili/cookie.txt", "dist/app.exe"):
            ignored = self.repo / filename
            ignored.parent.mkdir(parents=True, exist_ok=True)
            ignored.write_text("test-only", encoding="utf-8")
        message = '中文提交 "引号" & | < > %PATH% ! $env:PATH `whoami` $(Get-Date)'
        self.assert_uploaded(self.upload(message))
        self.assertEqual(self.git("log", "-1", "--format=%B"), message)
        self.assertEqual(self.git("show", "HEAD:tracked.txt"), "latest")
        self.assertEqual(self.git("show", "HEAD:中文 新文件.txt"), "new")
        tracked = self.git("ls-files").splitlines()
        for filename in ("deleted.txt", "config.json", ".env", "crash.log", "bili/cookie.txt", "dist/app.exe"):
            self.assertNotIn(filename, tracked)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_blank_message_uses_timestamp(self):
        (self.repo / "new.txt").write_text("new", encoding="utf-8")
        self.assert_uploaded(self.upload())
        self.assertRegex(self.git("log", "-1", "--format=%B"), r"^更新项目 \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

    def test_no_changes_pushes_pending_commit_and_can_repeat(self):
        original = self.git("rev-parse", "HEAD")
        self.assert_uploaded(self.upload())
        self.assert_uploaded(self.upload())
        self.assertEqual(self.git("rev-parse", "HEAD"), original)

    def test_failed_push_preserves_new_commit(self):
        self.git("remote", "set-url", "origin", str(self.root / "missing.git"))
        original = self.git("rev-parse", "HEAD")
        (self.repo / "new.txt").write_text("new", encoding="utf-8")
        result = self.upload("保留本地提交")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotEqual(self.git("rev-parse", "HEAD"), original)
        self.assertEqual(self.git("log", "-1", "--format=%B"), "保留本地提交")
        self.assertIn("本地提交已保留", result.stdout)

    def test_missing_origin_stops_before_staging(self):
        self.git("remote", "remove", "origin")
        (self.repo / "new.txt").write_text("new", encoding="utf-8")
        result = self.upload()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("未配置 origin", result.stdout)
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "")

    def test_missing_identity_stops_before_staging(self):
        self.git("config", "--unset", "user.email")
        result = self.upload()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("缺少提交邮箱", result.stdout)

    def test_detached_head_stops_before_staging(self):
        self.git("checkout", "--detach")
        result = self.upload()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("分离 HEAD", result.stdout)

    def test_batch_launcher_propagates_success_and_failure(self):
        self.assert_uploaded(self.upload(launcher=True))
        self.git("remote", "remove", "origin")
        result = self.upload(launcher=True)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
