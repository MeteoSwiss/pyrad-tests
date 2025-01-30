
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

def compare_csv_files(file1_path, file2_path, precision = 1E-3 ):
    df1 = pd.read_csv(file1_path, comment='#')
    df2 = pd.read_csv(file2_path, comment='#')

    # Check if the shape of the DataFrames match
    if df1.shape != df2.shape:
        return False
    if not all(df1.columns == df2.columns):
        return False
    
    if df1.shape[0] == 0:
        return True
    else:
        # Compare the values up to the desired precision
        diff = abs(df1 - df2)
        max_diff = diff.max().max()
        
        return max_diff <= precision

def compare_images(file1_path, file2_path, precision = 1E-3):
    # Open the NetCDF files
    im1 = imageio.imread(file1_path)
    im2 = imageio.imread(file2_path)
    are_equal = np.allclose(im1, im2, atol=precision)

    return are_equal

def compare_netcdf_files(file1_path, file2_path, precision = 1E-3):
    # Open the NetCDF files
    file1 = Dataset(file1_path, 'r')
    file2 = Dataset(file2_path, 'r')

    are_equal = True
    # Get the variable names from the files
    var_names = file1.variables.keys()

    for var_name in var_names:
        # Get the variable arrays from the files
        var1 = file1.variables[var_name][:].filled(0)
        var2 = file2.variables[var_name][:].filled(0)

        # Compare the arrays within the specified precision
        try:
            are_equal = np.allclose(var1, var2, atol=precision)
        except TypeError as e:
            are_equal = np.all(var1 == var2)

        if not are_equal:
            print(f'Variable {var_name} not equal in {file2_path}!')
            return are_equal
    
    # Close the NetCDF files
    file1.close()
    file2.close()
    return are_equal

def compare_directories(dir_a, dir_b):
    dir_comparison = filecmp.dircmp(dir_a, dir_b)

    if dir_comparison.left_only or dir_comparison.right_only:
        print(f'Dir {dir_a} and {dir_b} do not contain the same files')
        return False

    for common_dir in dir_comparison.common_dirs:
        new_dir_a = os.path.join(dir_a, common_dir)
        new_dir_b = os.path.join(dir_b, common_dir)
        if not compare_directories(new_dir_a, new_dir_b):
            return False

    for common_file in dir_comparison.common_files:
        file_a = os.path.join(dir_a, common_file)
        file_b = os.path.join(dir_b, common_file)
        print(f'Comparing files {file_a} and {file_b}')
        if '.nc' in common_file or '.NC' in common_file:
            are_equal = compare_netcdf_files(file_a, file_b)
            if not are_equal:
                return False
        elif '.png' in common_file or '.jpg' in common_file:
            continue
            # TODO implement image comparison that handles differences du to OS
            #are_equal = compare_images(file_a, file_b)
            #if not are_equal:
            #    return False
        elif '.csv' in common_file:
            are_equal = compare_csv_files(file_a, file_b)
            if not are_equal:
                return False

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
        print("No files found in the specified S3 path.")
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
        print("Invalid category value. Expected 'base' or 'mch'.")
        sys.exit(1)

    os.environ['PYART_CONFIG'] = os.path.join(os.environ['PYRAD_TESTS_PATH'], 'config', 'pyart',
        'mch_config.py')

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
        print(f'Running test {test_bname}')
        dir_test = os.path.join(directory_test, test_bname)
        dir_ref = os.path.join(directory_ref, test_bname)
        # Remove test dir if exists
        if os.path.exists(dir_test):
            shutil.rmtree(dir_test)
        if 'gecsx' in test:
            main_gecsx(test, gather_plots=False)
        else:
            t0 = time_ref[time_ref['test_name'] == test_bname]['t0']
            t1 = time_ref[time_ref['test_name'] == test_bname]['t1']
            starttime = datetime.datetime.strptime(str(int(t0)), '%Y%m%d%H%M%S')
            endtime = datetime.datetime.strptime(str(int(t1)), '%Y%m%d%H%M%S')
            main(test, starttime=starttime, endtime=endtime)            
            
        are_identical = compare_directories(dir_test,
                                            dir_ref)
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

