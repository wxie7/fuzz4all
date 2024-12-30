import os
import subprocess
from tqdm import tqdm
from pathlib import Path
import shutil
from datetime import datetime
import uuid
from threading import Thread

# Directories and paths from environment variables
rustc_path = os.path.join(os.getenv("RUSTC_INSTALL", ""), "bin", "rustc")
rustc_src_dir = os.getenv("RUSTC_SRC", "")
rustc_install = os.getenv("RUSTC_INSTALL", "")

# Validate required environment variables
if not os.path.isfile(rustc_path):
    raise FileNotFoundError(f"rustc executable not found at {rustc_path}. Ensure $RUSTC_INSTALL is correctly set.")
if not os.path.isdir(rustc_src_dir):
    raise NotADirectoryError(f"rustc source directory not found at {rustc_src_dir}. Ensure $RUSTC_SRC is correctly set.")

# Directories for hangs and crashes
temp_dir = os.getenv("TMPDIR", "/tmp")
hangs_dir = "hangs/rustc"
crashes_dir = "crashes/rustc"
coverage_dir = "coverage/rustc"
os.makedirs(temp_dir, exist_ok=True)
os.makedirs(hangs_dir, exist_ok=True)
os.makedirs(crashes_dir, exist_ok=True)
os.makedirs(coverage_dir, exist_ok=True)
# Target directory containing .fuzz files
fuzz_dir = "./Results/rustc/"

# Find all .fuzz files in the target directory and sort by modification time
fuzz_files = sorted(
    Path(fuzz_dir).glob("*.fuzz"),
    key=lambda f: os.path.getmtime(f)
)

# Group files by modification time into hourly bins without aligning to the hour
hourly_batches = []
hourly_summary = {}
if fuzz_files:
    current_batch = []
    first_file_time = os.path.getmtime(fuzz_files[0])
    current_hour_end = first_file_time + 3600  # First file's time + 1 hour

    for fuzz_file in fuzz_files:
        file_time = os.path.getmtime(fuzz_file)
        if file_time >= current_hour_end:  # New hour starts based on first file's time
            hourly_batches.append(current_batch)
            hourly_summary[current_hour_end - 3600] = len(current_batch)
            current_batch = []
            current_hour_end += 3600

        current_batch.append(fuzz_file)

    if current_batch:  # Add the last batch
        hourly_batches.append(current_batch)
        hourly_summary[current_hour_end - 3600] = len(current_batch)

# Display hourly summary
print("Hourly File Summary:")
for hour_start, count in hourly_summary.items():
    hour_start_time = datetime.fromtimestamp(hour_start).strftime('%Y-%m-%d %H:%M:%S')
    print(f"{hour_start_time}: {count} files")

tasks = []
for i, batch in enumerate(hourly_batches):
    suffix = f"{uuid.uuid4().hex[:4]}-{i}"
    temp_rustc_coverage_dir = os.path.join(temp_dir, f"fuzz4all/rustc/profraw-{suffix}")
    temp_rustc_object_dir = os.path.join(temp_dir, f"fuzz4all/rustc/object-{suffix}")
    tasks.append({
        "LLVM_PROFILE_FILE": temp_rustc_coverage_dir,
        "object_dir": temp_rustc_object_dir,
        "task_id": i + 1,
        "batch": batch
    })

for i, task in enumerate(tasks):
    task_id = task["task_id"]
    batch = task["batch"]
    llvm_profile_file = task["LLVM_PROFILE_FILE"]
    object_dir = task["object_dir"]
    print(f"task_id: {task_id}")
    print(f"batch size: {len(batch)}")
    print(f"prefix: {llvm_profile_file}")
    print(f"object_dir: {object_dir}")

compile_args = [
    "--crate-type", "staticlib",
    "-C", "link-dead-code",
    "-C", "debuginfo=2",
    "-C", "opt-level=3",
    "-Z", "mir-opt-level=3"
]

def has_ice_msg(msg):
    return "'rustc' panicked" in msg or "internal compiler error" in msg

def process_task(task):
    task_id = task["task_id"]
    batch = task["batch"]
    llvm_profile_file = task["LLVM_PROFILE_FILE"]
    object_dir = task["object_dir"]
    env = os.environ.copy()
    env["LLVM_PROFILE_FILE"] = f"{llvm_profile_file}/%p-%m.profraw"

    hangs_count = 0
    crashes_count = 0

    with tqdm(total=len(batch), desc=f"Task-{task_id}: Hangs: {hangs_count}, Crashes: {crashes_count}", unit="file") as pbar:
        for fuzz_file in batch:
            result = subprocess.run(
                ["timeout", "10", rustc_path] + compile_args + [fuzz_file, "-o", os.path.join(object_dir, f"out{os.path.basename(fuzz_file)}")],
                stderr=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                text=True,
                env=env
            )
            # Check exit code and stderr for conditions
            if result.returncode == 124:
                shutil.copy(str(fuzz_file), hangs_dir)
                hangs_count += 1
            elif has_ice_msg(result.stderr.lower()):
                shutil.copy(str(fuzz_file), crashes_dir)
                crashes_count += 1

            # Update progress bar description and increment
            pbar.set_description(f"Task-{task_id}: Hangs: {hangs_count}, Crashes: {crashes_count}")
            pbar.update(1)

    lcov_output_file = f"{coverage_dir}/cov_{task_id}.info"
    subprocess.run(
        ["grcov", llvm_profile_file, 
         "-s", os.path.join(rustc_src_dir, "compiler"), 
         "-b", rustc_install, 
         "--llvm-path", os.path.join(rustc_src_dir, "build/x86_64-unknown-linux-gnu/ci-llvm/bin"),
         "-t", "lcov", "-o", lcov_output_file],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    print(f"Task-{task_id}: Generated coverage file: {lcov_output_file}")

threads = []
for task in tasks:
    thread = Thread(target=process_task, args=(task,))
    threads.append(thread)
    thread.start()

# Wait for all threads to finish
for thread in threads:
    thread.join()

task_len = len(tasks)
for i in range(1, task_len):
    subprocess.run(
        ["lcov", "-a", f"{coverage_dir}/cov_{i}.info", "-a", f"{coverage_dir}/cov_{i + 1}.info", "-o", f"{coverage_dir}/cov_{i + 1}.info"]
    )

