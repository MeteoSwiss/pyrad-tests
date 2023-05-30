# pyrad-tests
Set of config files and reference outputs used for the CI testing of pyrad

## Guidelines on how to write new tests

1. Figure out if your test requires the [standard Py-ART version](https://github.com/ARM-DOE/pyart) or if it requires the [version of MeteoSwiss](https://github.com/MeteoSwiss/pyart).
2. Place your input data in pyrad-tests/input_data/<name_of_your_test>
3. Create your *loc*, *main* and *prod* config files under *pyrad-tests/config/processing/base/* if you use the standard Py-ART version or under *pyrad-tests/config/processing/mch/* if you use the MeteoSwiss version. Make sure to use the respect the following rules:
    * Main config filename must be <name_of_your_test>_main.txt
    * The *name* parameter in the main config file must be <name_of_your_test>
    * The main directory of the pyrad-tests repository must be indicated with ${PYRAD_TESTS_PATH}, for example *datapath STRING ${PYRAD_TESTS_PATH}/input_data/read_display_nexrad/KATX/*
    * Make sure that the path to the data as defined in your config files agrees with where you placed them at step 2.
    * Make sure that the output path is set to *saveimgbasepath STRING ${PYRAD_TESTS_PATH}/pyrad_products_test/*
4. Finally update the file *pyrad-tests/time_references.txt* with the relevant start and end times for your new test. Add the line <name_of_your_test>,start_time,end_time, start_time and end_time must be provided in the YYYYMMDDHHMMSS format.
5. Run your config files once using *main_process_data.py --cfgpath <local_path_to_pyrad_tests>/config/processing/<base or mch (see step 3)>/ --starttime start_time --endtime end_time <name_of_your_test>_main.txt*
6. If you are happy with your outputs and think they can be used as reference, copy the directory pyrad_products_test/<name_of_your_test> to pyrad_products_ref/<name_of_your_test
7. Add your local modifications to the github repository, with a PR
