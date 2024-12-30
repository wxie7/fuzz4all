import os
import subprocess
from tqdm import tqdm
from pathlib import Path
import shutil
from datetime import datetime
import uuid
from threading import Thread

# Directories and paths from environment variables
gcc_path = os.path.join(os.getenv("GCC_INSTALL", ""), "bin", "gcc")
gcc_build_dir = os.getenv("GCC_BUILD", "")

# Validate required environment variables
if not os.path.isfile(gcc_path):
    raise FileNotFoundError(f"GCC executable not found at {gcc_path}. Ensure $GCC_INSTALL is correctly set.")
if not os.path.isdir(gcc_build_dir):
    raise NotADirectoryError(f"GCC build directory not found at {gcc_build_dir}. Ensure $GCC_BUILD is correctly set.")

# Directories for hangs and crashes
temp_dir = os.getenv("TMPDIR", "/tmp/fuzz4all")
hangs_dir = "hangs/gcc"
crashes_dir = "crashes/gcc"
coverage_dir = "coverage/gcc"
os.makedirs(temp_dir, exist_ok=True)
os.makedirs(hangs_dir, exist_ok=True)
os.makedirs(crashes_dir, exist_ok=True)
os.makedirs(coverage_dir, exist_ok=True)
# Target directory containing .fuzz files
fuzz_dir = "./Results/gcc/"

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
    temp_gcc_build_dir = os.path.join(temp_dir, f"gcc-build-{uuid.uuid4().hex[:8]}")
    full_parts = temp_gcc_build_dir.strip(os.sep).split(os.sep)
    tasks.append({
        "GCOV_PREFIX": temp_gcc_build_dir,
        "GCOV_PREFIX_STRIP": str(len(full_parts) + 1),
        "task_id": i + 1,
        "batch": batch
    })

for i, task in enumerate(tasks):
    task_id = task["task_id"]
    batch = task["batch"]
    gcov_prefix = task["GCOV_PREFIX"]
    gcov_prefix_strip = task["GCOV_PREFIX_STRIP"]
    print(f"task_id: {task_id}")
    print(f"batch size: {len(batch)}")
    print(f"prefix: {gcov_prefix}")
    print(f"strip: {gcov_prefix_strip}")

def process_task(task):
    task_id = task["task_id"]
    batch = task["batch"]
    gcov_prefix = task["GCOV_PREFIX"]
    gcov_prefix_strip = task["GCOV_PREFIX_STRIP"]
    shutil.copytree(gcc_build_dir, gcov_prefix)
    env = os.environ.copy()
    env["GCOV_PREFIX"] = gcov_prefix
    env["GCOV_PREFIX_STRIP"] = gcov_prefix_strip
    subprocess.run(
        ["lcov", "-z", "-d", gcov_prefix],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    hangs_count = 0
    crashes_count = 0

    with tqdm(total=len(batch), desc=f"Task-{task_id}: Hangs: {hangs_count}, Crashes: {crashes_count}", unit="file") as pbar:
        for fuzz_file in batch:
            result = subprocess.run(
                ["timeout", "10", gcc_path, "-x", "c", "-std=c2x", "-c", str(fuzz_file), "-o", "/dev/null"],
                stderr=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                text=True,
                env=env
            )
            # Check exit code and stderr for conditions
            if result.returncode == 124:
                shutil.copy(str(fuzz_file), hangs_dir)
                hangs_count += 1
            elif "internal compiler error" in result.stderr.lower():
                shutil.copy(str(fuzz_file), crashes_dir)
                crashes_count += 1

            # Update progress bar description and increment
            pbar.set_description(f"Task-{task_id}: Hangs: {hangs_count}, Crashes: {crashes_count}")
            pbar.update(1)

    lcov_output_file = f"{coverage_dir}/cov_{task_id}.info"
    subprocess.run(
        ["lcov", "-c", "-d", gcov_prefix, "-o", lcov_output_file],
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

