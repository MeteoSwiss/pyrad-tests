
import sys
import pandas as pd
import glob
import os
import shutil
import datetime
from netCDF4 import Dataset
import numpy as np
from pyrad.flow import main, main_gecsx
from pyrad.io.config import read_config
import imageio
import filecmp
import os
import boto3
from contextlib import contextmanager, nullcontext
from pandas.api.types import is_numeric_dtype

BLUE = "\033[94m"
RED = "\033[91m"
RESET = "\033[0m"

ORIGINAL_cprint = print

def cprint(msg):
    ORIGINAL_cprint(f"{BLUE}{msg}{RESET}")

def cwarn(msg):
    ORIGINAL_cprint(f"{RED}{msg}{RESET}")

import builtins
from contextlib import contextmanager


def running_under_pytest() -> bool:
    return ("PYTEST_CURRENT_TEST" in os.environ) or ("pytest" in sys.modules)

#contextmanager
def suppress_external_stdout():
    original_print = builtins.print

    def fake_print(*args, **kwargs):
        # Allow PDB to print
        import inspect
        if any("pdb" in frame.filename for frame in inspect.stack()):
            return original_print(*args, **kwargs)
        # Otherwise suppress
        return None

    try:
        builtins.print = fake_print
        yield
    finally:
        builtins.print = original_print

ctx = suppress_external_stdout() if running_under_pytest() else nullcontext

def safe_to_numeric(df):
    for col in df.columns:
        try:
            df[col] = pd.to_numeric(df[col])
        except Exception:
            # Leave column unchanged if it cannot be converted
            pass
    return df

# =====================================================
# CSV COMPARISON
# =====================================================
def compare_csv_files(file1_path, file2_path, precision=1e-3):
    df1 = pd.read_csv(file1_path, comment="#")
    df2 = pd.read_csv(file2_path, comment="#")

    # Shape / column mismatch
    if df1.shape != df2.shape:
        cwarn(f"CSV shape mismatch: {file1_path} vs {file2_path}")
        cwarn(f"df1 shape={df1.shape}, df2 shape={df2.shape}")
        return False

    if not df1.columns.equals(df2.columns):
        cwarn(f"CSV columns differ in {file1_path} vs {file2_path}")
        cwarn(f"df1 columns={list(df1.columns)}")
        cwarn(f"df2 columns={list(df2.columns)}")
        return False

    if len(df1) == 0:
        return True

    # Convert numeric columns where possible
    df1 = safe_to_numeric(df1)
    df2 = safe_to_numeric(df2)

    # Compare column by column
    for col in df1.columns:
        s1 = df1[col]
        s2 = df2[col]

        if is_numeric_dtype(s1) and is_numeric_dtype(s2):
            if not np.allclose(s1, s2, rtol=precision, atol=precision, equal_nan=True):
                diff = np.nanmax(np.abs(s1 - s2))
                cwarn(f"CSV numeric column '{col}' differs")
                cwarn(f"Max difference: {diff}")
                return False
        else:
            if not s1.equals(s2):
                cwarn(f"CSV non-numeric column '{col}' differs")
                return False

    return True


# =====================================================
# IMAGE COMPARISON
# =====================================================
def compare_images(file1_path, file2_path, precision=1e-3):
    im1 = imageio.imread(file1_path)
    im2 = imageio.imread(file2_path)

    if im1.shape != im2.shape:
        cwarn(f"Image shape mismatch: {file1_path} vs {file2_path}")
        return False

    if not np.allclose(im1, im2, atol=precision):
        diff = np.nanmax(np.abs(im1.astype(float) - im2.astype(float)))
        cwarn(f"Images differ: {file1_path} vs {file2_path}")
        cwarn(f"Max pixel diff: {diff}")
        return False

    return True


# =====================================================
# NETCDF COMPARISON
# =====================================================
def compare_netcdf_files(file1_path, file2_path, precision=1e-3):
    with Dataset(file1_path, 'r') as nc1, Dataset(file2_path, 'r') as nc2:

        vars1 = set(nc1.variables.keys())
        vars2 = set(nc2.variables.keys())

        if vars1 != vars2:
            cwarn(f"NetCDF variable mismatch: {file1_path} vs {file2_path}")
            cwarn(f"Only in file1: {vars1 - vars2}")
            cwarn(f"Only in file2: {vars2 - vars1}")
            return False

        for var_name in vars1:
            v1 = nc1.variables[var_name][:]
            v2 = nc2.variables[var_name][:]

            # Masked arrays → fill with NaN
            a1 = np.array(v1.filled(np.nan))
            a2 = np.array(v2.filled(np.nan))

            if a1.shape != a2.shape:
                cwarn(f"NetCDF variable '{var_name}' shape mismatch")
                cwarn(f"a1 shape={a1.shape}, a2 shape={a2.shape}")
                return False

            try:
                equal = np.allclose(a1, a2, atol=precision, equal_nan=True)
            except TypeError:
                equal = np.array_equal(a1, a2)

            if not equal:
                diff = np.nanmax(np.abs(a1 - a2))
                cwarn(f"NetCDF variable '{var_name}' differs")
                cwarn(f"Max difference: {diff}")
                return False

    return True


# =====================================================
# DIRECTORY COMPARISON
# =====================================================
def compare_directories(dir_a, dir_b):
    cmp = filecmp.dircmp(dir_a, dir_b)

    if cmp.left_only or cmp.right_only:
        cwarn(f"Directory mismatch: {dir_a} vs {dir_b}")
        cwarn(f"Only in {dir_a}: {cmp.left_only}")
        cwarn(f"Only in {dir_b}: {cmp.right_only}")
        return False

    # Recurse into subdirectories
    for d in cmp.common_dirs:
        if not compare_directories(os.path.join(dir_a, d), os.path.join(dir_b, d)):
            return False

    # Compare actual files
    for f in cmp.common_files:
        a = os.path.join(dir_a, f)
        b = os.path.join(dir_b, f)

        if f.lower().endswith(".nc"):
            if not compare_netcdf_files(a, b):
                cwarn(f"NetCDF file mismatch: {a} vs {b}")
                return False

        elif f.lower().endswith(".csv"):
            if not compare_csv_files(a, b):
                cwarn(f"CSV file mismatch: {a} vs {b}")
                return False

        elif f.lower().endswith((".png", ".jpg", ".jpeg")):
            # TODO: enable later
            continue

        else:
            cwarn(f"Skipping unsupported file: {f}")

    return True


def compare_s3_local(local_dir: str, s3_cfg: dict, size_tolerance=0.05) -> bool:
    """
    Compare the content of a local directory and an S3 bucket.

    Args:
        local_dir (str): Path to the local directory.
        s3_cfg (dict): S3 configuration with keys "bucket", "endpoint", and "path".
        size_tolerance (float): Allowed relative difference in file size (default 5%).

    Returns:
        True if the content is the same, False otherwise
    """
    mismatches = []

    # Extract S3 configuration details
    bucket = s3_cfg["bucket"]
    endpoint = s3_cfg["endpoint"]
    s3_path = s3_cfg["path"].strip("/")

    aws_key = os.environ["S3_KEY_WRITE"]
    aws_secret = os.environ["S3_SECRET_WRITE"]

    s3_client_config = {
        "aws_access_key_id": aws_key,
        "endpoint_url": endpoint,
        "aws_secret_access_key": aws_secret,
    }
    
    # Initialize S3 client
    s3_client = boto3.client("s3", **s3_client_config, verify=False)
    
    # List objects in the S3 path
    s3_objects = s3_client.list_objects_v2(Bucket=bucket, Prefix=s3_path)
    if "Contents" not in s3_objects:
        cprint("No files found in the specified S3 path.")
        return []

    # Build a dictionary of S3 files and their sizes
    s3_files = {
        obj["Key"].replace(f"{s3_path}/", ""): obj["Size"]
        for obj in s3_objects["Contents"]
        if not obj["Key"].endswith("/")
    }
    # Walk through the local directory
    for root, _, files in os.walk(local_dir):
        for file_name in files:
            local_file_path = os.path.join(root, file_name)
            relative_path = os.path.relpath(local_file_path, local_dir)

            # Skip if not present in S3
            if relative_path not in s3_files:
                mismatches.append(f"Missing in S3: {relative_path}")
                continue

            # Compare file sizes
            local_size = os.path.getsize(local_file_path)
            s3_size = s3_files[relative_path]
            size_diff = abs(local_size - s3_size) / max(local_size, s3_size)

            if size_diff > size_tolerance:
                mismatches.append(
                    f"Size mismatch for {relative_path}: local={local_size} bytes, s3={s3_size} bytes"
                )
    return ~len(mismatches)


def run_tests(category):
    # Validate category value
    if category != "base" and category != "mch":
        cprint("Invalid category value. Expected 'base' or 'mch'.")
        sys.exit(1)

    directory_test = os.path.join(os.environ['PYRAD_TESTS_PATH'], 
                                  'pyrad_products_test/')
    directory_ref = os.path.join(os.environ['PYRAD_TESTS_PATH'], 
                                 'pyrad_products_ref/')
    # Read time references CSV file
    filename = os.path.join(os.environ['PYRAD_TESTS_PATH'], "time_references.txt")
    time_ref = pd.read_csv(filename, comment = '#')

    # Get all tests
    all_tests = glob.glob(os.environ['PYRAD_TESTS_PATH'] + 
            f'./config/processing/{category}/*main*')

    for test in all_tests:
        test_bname = os.path.basename(test).split('_main')[0]
        
        cprint('\n=======================')
        cprint(f'Running test {test_bname}')
        cprint('=======================')
        dir_test = os.path.join(directory_test, test_bname)
        dir_ref = os.path.join(directory_ref, test_bname)
        # Remove test dir if exists
        if os.path.exists(dir_test):
            shutil.rmtree(dir_test)
        if 'gecsx' in test:
            with ctx():
                main_gecsx(test, gather_plots=False)
        else:
            t0 = time_ref[time_ref['test_name'] == test_bname]['t0']
            t1 = time_ref[time_ref['test_name'] == test_bname]['t1']
            starttime = datetime.datetime.strptime(str(int(t0.iloc[0])), '%Y%m%d%H%M%S').replace(tzinfo=datetime.timezone.utc)
            endtime = datetime.datetime.strptime(str(int(t1.iloc[0])), '%Y%m%d%H%M%S').replace(tzinfo=datetime.timezone.utc)
            with ctx():
                cprint("Starting test ")
                main(test, starttime=starttime, endtime=endtime)            
            
        are_identical = compare_directories(dir_test,
                                            dir_ref)
        if are_identical:
            cprint(f'Test {test_bname} passed!')
            
        assert are_identical
        
        if "s3" in test:
            # Check content of S3 so it matches reference
            cfg = read_config(test)
            cfg_s3 = {"bucket": cfg["s3BucketWrite"],
                      "endpoint": cfg["s3EndpointWrite"],
                      "path": cfg.get("s3PathWrite", "") + '/' + cfg["name"]}
            are_identical = compare_s3_local(dir_ref,
                                            cfg_s3)
            
            assert are_identical

def test_base():
    run_tests('base')

def test_mch():
    run_tests('mch')

if __name__ == "__main__":
    test_name = sys.argv[1]
    run_tests(test_name)
