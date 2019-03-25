#!/usr/bin/env python
# encoding: utf-8
'''
auditok.auditok -- Audio Activity Detection tool

auditok.auditok is a program that can be used for Audio/Acoustic activity detection.
It can read audio data from audio files as well as from built-in device(s) or standard input 


@author:     Mohamed El Amine SEHILI

@copyright:  2015-2018 Mohamed El Amine SEHILI

@license:    GPL v3

@contact:    amine.sehili@gmail.com
@deffield    updated: 01 Nov 2018
'''

import sys
import os

from optparse import OptionParser, OptionGroup
from threading import Thread
import tempfile
import wave
import time
import threading
import logging

try:
    import future
    from queue import Queue, Empty
except ImportError:
    if sys.version_info >= (3, 0):
        from queue import Queue, Empty
    else:
        from Queue import Queue, Empty

try:
    from pydub import AudioSegment
    WITH_PYDUB = True
except ImportError:
    WITH_PYDUB = False
    

from core import StreamTokenizer
from io1 import PyAudioSource, BufferAudioSource, StdinAudioSource, player_for
from util import ADSFactory, AudioEnergyValidator
#from auditok import __version__ as version

__all__ = []
version = 1.0
__version__ = version

__date__ = '2015-11-23'
__updated__ = '2018-10-06'

DEBUG = 0
TESTRUN = 1
PROFILE = 0

LOGGER_NAME = "AUDITOK_LOGGER"

class AudioFileFormatError(Exception):
    pass

class TimeFormatError(Exception):
    pass



def seconds_to_str_fromatter(_format):
    """
    Accepted format directives: %i %s %m %h
    """
    # check directives are correct 
    
    if _format == "%S":
        def _fromatter(seconds):
            return "{:.2f}".format(seconds)
    
    elif _format == "%I":
        def _fromatter(seconds):
            return "{0}".format(int(seconds * 1000))
    
    else:
        _format = _format.replace("%h", "{hrs:02d}")
        _format = _format.replace("%m", "{mins:02d}")
        _format = _format.replace("%s", "{secs:02d}")
        _format = _format.replace("%i", "{millis:03d}")
        
        try:
            i = _format.index("%")
            raise TimeFormatError("Unknow time format directive '{0}'".format(_format[i:i+2]))
        except ValueError:
            pass
        
        def _fromatter(seconds):
            millis = int(seconds * 1000)
            hrs, millis = divmod(millis, 3600000)
            mins, millis = divmod(millis, 60000)
            secs, millis = divmod(millis, 1000)
            return _format.format(hrs=hrs, mins=mins, secs=secs, millis=millis)
    
    return _fromatter



class Worker(Thread):
    
    def __init__(self, timeout=0.2, debug=False, logger=None):
        self.timeout = timeout
        self.debug = debug
        self.logger = logger
        
        if self.debug and self.logger is None:
            self.logger = logging.getLogger(LOGGER_NAME)
            self.logger.setLevel(logging.DEBUG)
            handler = logging.StreamHandler(sys.stdout)
            self.logger.addHandler(handler)
            
        self._inbox = Queue()
        self._stop_request = Queue()
        Thread.__init__(self)
    
    
    def debug_message(self, message):
        self.logger.debug(message)
        
    def _stop_requested(self):
        
        try:
            message = self._stop_request.get_nowait()
            if message == "stop":
                return True

        except Empty:
            return False
    
    def stop(self):
        self._stop_request.put("stop")
        self.join()
        
    def send(self, message):
        self._inbox.put(message)
    
    def _get_message(self):
        try:
            message = self._inbox.get(timeout=self.timeout)
            return message        
        except Empty:
            return None


class TokenizerWorker(Worker):
    
    END_OF_PROCESSING = "END_OF_PROCESSING"
    
    def __init__(self, ads, tokenizer, analysis_window, observers):
        self.ads = ads
        self.tokenizer = tokenizer
        self.analysis_window = analysis_window
        self.observers = observers
        self._inbox = Queue()
        self.count = 0
        Worker.__init__(self)
        
    def run(self):
        
        def notify_observers(data, start, end):
            audio_data = b''.join(data)
            self.count += 1
            
            start_time = start * self.analysis_window
            end_time = (end+1) * self.analysis_window
            duration = (end - start + 1) * self.analysis_window
            
            # notify observers
            for observer in self.observers:
                observer.notify({"id" : self.count,
                                 "audio_data" : audio_data,
                                 "start" : start,
                                 "end" : end,
                                 "start_time" : start_time,
                                 "end_time" : end_time,
                                 "duration" : duration}
                                )
        
        self.ads.open()
        self.tokenizer.tokenize(data_source=self, callback=notify_observers)
        for observer in self.observers:
            observer.notify(TokenizerWorker.END_OF_PROCESSING)
            
    def add_observer(self, observer):
        self.observers.append(observer)
       
    def remove_observer(self, observer):
        self.observers.remove(observer)
    
    def read(self):
        if self._stop_requested():
            return None
        else:
            return self.ads.read()
    

class LogWorker(Worker):
    
    def __init__(self, print_detections=False, output_format="{start} {end}",
                 time_formatter=seconds_to_str_fromatter("%S"), timeout=0.2, debug=False, logger=None):
        
        self.print_detections = print_detections
        self.output_format = output_format
        self.time_formatter = time_formatter
        self.detections = []
        Worker.__init__(self, timeout=timeout, debug=debug, logger=logger)
        
    def run(self):
        while True:
            if self._stop_requested():
                break
            
            message = self._get_message()
            
            if message is not None:
                
                if message == TokenizerWorker.END_OF_PROCESSING:
                    break
                
                audio_data = message.pop("audio_data", None)
                _id = message.pop("id", None)
                start = message.pop("start", None)
                end = message.pop("end", None)
                start_time = message.pop("start_time", None)
                end_time = message.pop("end_time", None)
                duration = message.pop("duration", None)
                if audio_data is not None and len(audio_data) > 0:
                    
                    if self.debug:
                        self.debug_message("[DET ]: Detection {id} (start:{start}, end:{end})".format(id=_id, 
                            start="{:5.2f}".format(start_time),
                            end="{:5.2f}".format(end_time)))
                    
                    if self.print_detections:
                        print(self.output_format.format(id = _id,
                            start = self.time_formatter(start_time),
                            end = self.time_formatter(end_time), duration = self.time_formatter(duration)))
                    

                    self.detections.append(("id :",  _id, start, end, start_time, end_time))
                   
    
    def notify(self, message):
        # pass
        print ("Call the vision function here") 
        
        self.send(message)



def speechActivityDetector():

    # program_name = os.path.basename(sys.argv[0])
    # program_version = version
    # program_build_date = "%s" % __updated__

    # program_version_string = '%%prog %s (%s)' % (program_version, program_build_date)
    #program_usage = '''usage: spam two eggs''' # optional - will be autogenerated by optparse
    # program_longdesc = '''''' # optional - give further explanation about what the program does
    # program_license = "Copyright 2015-2018 Mohamed El Amine SEHILI                                            \
    #             Licensed under the General Public License (GPL) Version 3 \nhttp://www.gnu.org/licenses/"

    # if argv is None:
    #     argv = sys.argv[1:]
    try:


        opts_input = None
        opts_input_type = None
        opts_max_time = None
        opts_output_main = None
        opts_output_tokens = None
        opts_output_type = None
        opts_use_channel = 1  

        opts_analysis_window = 0.01 
        opts_min_duration = 0.2
        opts_max_duration = 0.5
        opts_max_silence = 0.3
        opts_drop_trailing_silence = False
        opts_energy_threshold= 63

        opts_sampling_rate = 16000
        opts_channels = 1
        opts_sample_width =2 
        opts_input_device_index=None
        opts_frame_per_buffer = 1024


        opts_command = None
        opts_echo = False
        opts_plot = False
        opts_save_image = None 
        opts_printf= "{id} {start} {end}"
        opts_time_format = "%S"


        opts_quiet = False
        opts_debug = False
        opts_debug_file = None



        try:
            asource = PyAudioSource(sampling_rate = opts_sampling_rate,
                                    sample_width = opts_sample_width,
                                    channels = opts_channels,
                                    frames_per_buffer = opts_frame_per_buffer,
                                    input_device_index = opts_input_device_index)

        except Exception:
            sys.stderr.write("Cannot read data from audio device!\n")
            sys.stderr.write("You should either install pyaudio or read data from STDIN\n")
            sys.exit(2)

        logger = logging.getLogger(LOGGER_NAME)
        logger.setLevel(logging.DEBUG)

        handler = logging.StreamHandler(sys.stdout)
        
        logger.addHandler(handler)
        
        record = opts_output_main is not None or opts_plot or opts_save_image is not None
                        
        ads = ADSFactory.ads(audio_source = asource, block_dur = opts_analysis_window, max_time = opts_max_time, record = record)
        validator = AudioEnergyValidator(sample_width=asource.get_sample_width(), energy_threshold=opts_energy_threshold)
        
        
        if opts_drop_trailing_silence:
            mode = StreamTokenizer.DROP_TRAILING_SILENCE
        else:
            mode = 0
        
        analysis_window_per_second = 1. / opts_analysis_window
        tokenizer = StreamTokenizer(validator=validator, min_length=opts_min_duration * analysis_window_per_second,
                                    max_length=int(opts_max_duration * analysis_window_per_second),
                                    max_continuous_silence=opts_max_silence * analysis_window_per_second,
                                    mode = mode)
        
        
        observers = []
        tokenizer_worker = None
        

        if not opts_quiet or opts_plot is not None or opts_save_image is not None:    
            oformat = opts_printf.replace("\\n", "\n").replace("\\t", "\t").replace("\\r", "\r")
            
            converter = seconds_to_str_fromatter(opts_time_format)

            log_worker = LogWorker(print_detections = not opts_quiet, output_format=oformat,
                                   time_formatter=converter, logger=logger, debug=opts_debug)


            observers.append(log_worker)

        tokenizer_worker = TokenizerWorker(ads, tokenizer, opts_analysis_window, observers)
        

        # start observer threads
        for obs in observers:
            obs.start()
        # start tokenization thread
        
        tokenizer_worker.start()
        
        while True:
            time.sleep(1)
            if len(threading.enumerate()) == 1:
                break
            
        tokenizer_worker = None
            
            
        return 0
                    
    except KeyboardInterrupt:
        
        if tokenizer_worker is not None:
            tokenizer_worker.stop()
        for obs in observers:
            obs.stop()
            
        if opts_output_main is not None:
            _save_main_stream()
        if opts_plot or opts_save_image is not None:
            _plot()
        
        return 0

    except Exception as e:
        # sys.stderr.write(program_name + ": " + str(e) + "\n")
        sys.stderr.write("for help use -h\n")
        
        return 2




speechActivityDetector()
