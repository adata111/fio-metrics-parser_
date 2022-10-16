"""Extracts required metrics from fio output file.

   Takes fio output json filepath as command-line input
   Extracts IOPS, Bandwidth and Latency (min, max, mean) from given input file

   Usage from dashboard folder:
    python3 -m fio.fio_metrics <path to fio output json file>

"""

import json
import re
import sys
from typing import Any, Dict, List

JOBNAME = 'jobname'
GLOBAL_OPTS = 'global options'
JOBS = 'jobs'
JOB_OPTS = 'job options'
FILESIZE = 'filesize'
NUMJOBS = 'numjobs'
THREADS = 'num_threads'
TIMESTAMP_MS = 'timestamp_ms'
RUNTIME = 'runtime'
RAMPTIME = 'ramp_time'
START_TIME = 'start_time'
END_TIME = 'end_time'
READ = 'read'
IOPS = 'iops'
BW = 'bw'
LAT = 'lat_ns'
MIN = 'min'
MAX = 'max'
MEAN = 'mean'

FILESIZE_CONVERSION = {
    'b': 0.001,
    'k': 1,
    'kb': 1,
    'm': 10**3,
    'mb': 10**3,
    'g': 10**6,
    'gb': 10**6,
    't': 10**9,
    'tb': 10**9,
    'p': 10**12,
    'pb': 10**12
}

RAMPTIME_CONVERSION = {
    'us': 10**(-3),
    'ms': 1,
    's': 1000,
    'm': 60*1000,
    'h': 3600*1000,
    'd': 24*3600*1000
}


def _convert_value(value, conversion_dict):
  """Converts data strings to a particular unit based on conversion_dict.

  Args:
    value: String, contains data value+unit
    conversion_dict: Dictionary containing units and their respective
      multiplication factor

  Returns:
    Int, number in a specific unit

  Ex: For args value = "5s" and conversion_dict=RAMPTIME_CONVERSION 
      "5s" will be converted to 5000 milliseconds and 5000 will be returned

  """
  num, unit = re.findall('[0-9]+|[A-Za-z]+', value)
  mult_factor = conversion_dict[unit.lower()]
  converted_num = int(num) * mult_factor
  return converted_num

class NoValuesError(Exception):
  """Some data is missing from the json output file."""


class FioMetrics:
  """Handles logic related to parsing fio output and writing them to google sheet.

  """

  def _load_file_dict(self, filepath) -> Dict[str, Any]:
    """Reads json data from given filepath and returns json object.

    Args:
      filepath : str
        Path of the json file to be parsed

    Returns:
      JSON object, contains json data loaded from given filepath

    Raises:
      OSError: If input filepath doesn't exist
      ValueError: file is not in proper JSON format
      NoValuesError: file doesn't contain JSON data

    """
    fio_out = {}
    f = open(filepath, 'r')
    try:
      fio_out = json.load(f)
    except ValueError as e:
      raise e
    finally:
      f.close()

    if not fio_out:  # Empty JSON object
      raise NoValuesError(f'JSON file {filepath} returned empty object')
    return fio_out

  def _extract_metrics(self, fio_out) -> List[Dict[str, Any]]:
    """Extracts and returns required metrics from fio output dict.

      The extracted metrics are stored in a list. Each entry in the list is a
      dictionary. Each dictionary stores the following fio metrics related
      to a particualar job:
        jobname, filesize, number of threads, IOPS, Bandwidth and latency (min,
        max and mean)

    Args:
      fio_out: JSON object representing the fio output

    Returns:
      List of dicts, contains list of jobs and required metrics for each job
      Example return value:
        [{'jobname': '1_thread', 'filesize': 5000, 'num_threads':40,
        'start_time':1653027155, 'end_time':1653027215,
        'iops': 85.137657, 'bw': 99137, 'lat_ns': {'min': 365421594,
        'max': 38658496964, 'mean': 23292225875.57558}}]

    Raises:
      KeyError: Key is missing in the json output
      NoValuesError: Data not present in json object

    """

    if not fio_out:
      raise NoValuesError('No data in json object')

    global_filesize = ''
    global_ramptime_ms = 0
    if GLOBAL_OPTS in fio_out:
      if FILESIZE in fio_out[GLOBAL_OPTS]:
        global_filesize = fio_out[GLOBAL_OPTS][FILESIZE]
      if RAMPTIME in fio_out[GLOBAL_OPTS]:
        global_ramptime_ms = _convert_value(fio_out[GLOBAL_OPTS][RAMPTIME],
                                            RAMPTIME_CONVERSION)

    all_jobs = []
    prev_endtime_s = 0
    for i, job in enumerate(fio_out[JOBS]):
      jobname = ''
      iops = bw_kibps = min_lat_ns = max_lat_ns = mean_lat_ns = filesize = 0
      jobname = job[JOBNAME]
      job_read = job[READ]

      iops = job_read[IOPS]
      bw_kibps = job_read[BW]
      min_lat_ns = job_read[LAT][MIN]
      max_lat_ns = job_read[LAT][MAX]
      mean_lat_ns = job_read[LAT][MEAN]

      # default calue of numjobs
      numjobs = '1'
      ramptime_ms = 0
      if JOB_OPTS in job:
        if NUMJOBS in job[JOB_OPTS]:
          numjobs = job[JOB_OPTS][NUMJOBS]

        if FILESIZE in job[JOB_OPTS]:
          filesize = job[JOB_OPTS][FILESIZE]

        if RAMPTIME in job[JOB_OPTS]:
          ramptime_ms = _convert_value(job[JOB_OPTS][RAMPTIME],
                                       RAMPTIME_CONVERSION)

      if not filesize:
        filesize = global_filesize

      if ramptime_ms == 0:
        ramptime_ms = global_ramptime_ms

      # for multiple jobs, start time of one job = end time of previous job
      start_time_ms = prev_endtime_s * 1000 if prev_endtime_s > 0 else fio_out[
          TIMESTAMP_MS]
      # endtime = job start time + job runtime + ramp time
      end_time_ms = start_time_ms + job_read[RUNTIME] + ramptime_ms

      # converting start and end time to seconds
      start_time_s = start_time_ms // 1000
      end_time_s = round(end_time_ms/1000)
      prev_endtime_s = end_time_s

      # If jobname and filesize are empty OR start_time=end_time
      # OR all the metrics are zero, log skip warning and continue to next job
      if ((not jobname and not filesize) or (start_time_s == end_time_s) or
          (not iops and not bw_kibps and not min_lat_ns and not max_lat_ns and
           not mean_lat_ns)):
        # TODO(ahanadatta): Print statement will be replaced by logging.
        print(f'No job details or metrics in json, skipping job index {i}')
        prev_endtime_s = 0
        continue

      filesize_kb = _convert_value(filesize, FILESIZE_CONVERSION)

      numjobs = int(numjobs)

      all_jobs.append({
          JOBNAME: jobname,
          FILESIZE: filesize_kb,
          THREADS: numjobs,
          START_TIME: start_time_s,
          END_TIME: end_time_s,
          IOPS: iops,
          BW: bw_kibps,
          LAT: {MIN: min_lat_ns, MAX: max_lat_ns, MEAN: mean_lat_ns}
      })

    if not all_jobs:
      raise NoValuesError('No data could be extracted from file')

    return all_jobs

  def get_metrics(self, filepath) -> List[Dict[str, Any]]:
    """Returns job metrics obtained from given filepath and writes to gsheets.

    Args:
      filepath : str
        Path of the json file to be parsed

    Returns:
      List of dicts, contains list of jobs and required metrics for each job
    """
    fio_out = self._load_file_dict(filepath)
    job_metrics = self._extract_metrics(fio_out)

    return job_metrics

if __name__ == '__main__':
  argv = sys.argv
  if len(argv) != 2:
    raise TypeError('Incorrect number of arguments.\n'
                    'Usage: '
                    'python3 fio_metrics.py <fio output json filepath>')

  fio_metrics_obj = FioMetrics()
  temp = fio_metrics_obj.get_metrics(argv[1])
  print(temp)

