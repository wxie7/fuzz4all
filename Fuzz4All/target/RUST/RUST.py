import os
import subprocess

from Fuzz4All.target.target import FResult, Target
from Fuzz4All.util.util import comment_remover


class RustTarget(Target):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.SYSTEM_MESSAGE = "You are a Rust Fuzzer"
        if kwargs["template"] == "fuzzing_with_config_file":
            config_dict = kwargs["config_dict"]
            self.prompt_used = self._create_prompt_from_config(config_dict)
            self.config_dict = config_dict
        else:
            raise NotImplementedError
        self.temp_dir = os.environ.get("TMPDIR", "/tmp")
        self.cov_dir = os.path.join(self.temp_dir, "fuzz4all/rust/coverage")
        self.obj_dir = os.path.join(self.temp_dir, "fuzz4all/rust/object")
        self.code_dir = os.path.join(self.temp_dir, "fuzz4all/rust/code")
        os.makedirs(self.temp_dir, exist_ok=True)
        os.makedirs(self.cov_dir, exist_ok=True)
        os.makedirs(self.obj_dir, exist_ok=True)
        os.makedirs(self.code_dir, exist_ok=True)

    def write_back_file(self, code):
        filepath = os.path.join(self.code_dir, "temp2024.rs")
        try:
            with open(
                filepath, "w", encoding="utf-8"
            ) as f:
                f.write(code)
        except:
            pass
        return filepath

    def wrap_prompt(self, prompt: str) -> str:
        return f"/* {prompt} */\n{self.prompt_used['separator']}\n{self.prompt_used['begin']}"

    def wrap_in_comment(self, prompt: str) -> str:
        return f"/* {prompt} */"

    def filter(self, code) -> bool:
        clean_code = code.replace(self.prompt_used["begin"], "").strip()
        if self.prompt_used["target_api"] not in clean_code:
            return False
        return True

    def clean(self, code: str) -> str:
        code = comment_remover(code)
        return code

    # remove any comments, or blank lines
    def clean_code(self, code: str) -> str:
        code = comment_remover(code)
        code = "\n".join(
            [
                line
                for line in code.split("\n")
                if line.strip() != "" and line.strip() != self.prompt_used["begin"]
            ]
        )
        return code

    def has_ice_msg(self, msg):
        return "'rustc' panicked" in msg or "internal compiler error" in msg


    def validate_compiler(self, compiler, filename) -> (FResult, str):

        env = os.environ.copy()
        env["LLVM_PROFILE_FILE"] = "/dev/null"
        flags = ["--crate-type", "staticlib",
                "-C", "link-dead-code",
                "-C", "debuginfo=2",
                "-C", "opt-level=3",
                "-Z", "mir-opt-level=3"]

        output_file = os.path.join(self.obj_dir, os.path.basename(filename) + ".o")
        cmd = ' '.join([compiler] + flags + [filename] + ['-o', output_file])
        cmd = "timeout 30 " + cmd

        try:
            exit_code = subprocess.run(
                cmd,
                shell=True,
                env=env,
                capture_output=True,
                encoding="utf-8",
                timeout=30,
                text=True,
            )

            if exit_code.returncode == 124:
                return FResult.TIMED_OUT, compiler

        except UnicodeDecodeError as ue:
            return FResult.FAILURE, compiler

        if exit_code.returncode != 0:
            return FResult.FAILURE, exit_code.stderr

        return FResult.SAFE, "its safe"

    def validate_individual(self, filename) -> (FResult, str):
        fresult, msg = self.validate_compiler(self.target_name, filename)
        if fresult == FResult.SAFE:
            return FResult.SAFE, "its safe"
        elif fresult == FResult.ERROR:
            return FResult.ERROR, f"{msg}"
        elif fresult == FResult.TIMED_OUT:
            return FResult.ERROR, "timed out"
        elif fresult == FResult.FAILURE:
            return FResult.FAILURE, f"{msg}"
        else:
            return (FResult.TIMED_OUT,)
