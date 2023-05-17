
import sys
import pandas as pd
import glob
import os
import shutil
import datetime
from netCDF4 import Dataset
import numpy as np
from pyrad.flow import main

import imageio
import filecmp
import os

def compare_csv_files(file1_path, file2_path, precision = 1E-6 ):
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

def compare_images(file1_path, file2_path, precision = 1E-6):
    # Open the NetCDF files
    im1 = imageio.imread(file1_path)
    im2 = imageio.imread(file2_path)

    are_equal = np.allclose(im1, im2, atol=precision)

    return are_equal

def compare_netcdf_files(file1_path, file2_path, precision = 1E-6):
    # Open the NetCDF files
    file1 = Dataset(file1_path, 'r')
    file2 = Dataset(file2_path, 'r')

    are_equal = True
    # Get the variable names from the files
    var_names = file1.variables.keys()

    for var_name in var_names:
        # Get the variable arrays from the files
        var1 = np.array(file1.variables[var_name][:])
        var2 = np.array(file2.variables[var_name][:])

        # Compare the arrays within the specified precision
        try:
            are_equal = np.allclose(var1, var2, atol=precision)
        except TypeError as e:
            are_equal = np.all(var1 == var2)

        if not are_equal:
            return are_equal
    
    # Close the NetCDF files
    file1.close()
    file2.close()
    return are_equal

def compare_directories(dir_a, dir_b):
    dir_comparison = filecmp.dircmp(dir_a, dir_b)

    if dir_comparison.left_only or dir_comparison.right_only:
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
            are_equal = compare_images(file_a, file_b)
            if not are_equal:
                return False
        elif '.csv' in common_file:
            are_equal = compare_csv_files(file_a, file_b)
            if not are_equal:
                return False

    return True

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
        t0 = time_ref[time_ref['test_name'] == test_bname]['t0']
        t1 = time_ref[time_ref['test_name'] == test_bname]['t1']
        starttime = datetime.datetime.strptime(str(int(t0)), '%Y%m%d%H%M%S')
        endtime = datetime.datetime.strptime(str(int(t1)), '%Y%m%d%H%M%S')
        main(test, starttime=starttime, endtime=endtime)

        are_identical = compare_directories(dir_test,
                                            dir_ref)
        assert are_identical


def test_base():
    run_tests('base')

def test_mch():
    run_tests('mch')

