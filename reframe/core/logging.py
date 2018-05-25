import abc
import collections.abc
import logging
import logging.handlers
import numbers
import os
import pprint
import shutil
import sys
import warnings
from datetime import datetime

import reframe
import reframe.core.debug as debug
from reframe.core.exceptions import ConfigError, LoggingError
from reframe.settings import settings

# Reframe's log levels
CRITICAL = 50
ERROR    = 40
WARNING  = 30
INFO     = 20
VERBOSE  = 19
DEBUG    = 10
NOTSET   = 0


_log_level_names = {
    CRITICAL: 'critical',
    ERROR:    'error',
    WARNING:  'warning',
    INFO:     'info',
    VERBOSE:  'verbose',
    DEBUG:    'debug',
    NOTSET:   'undefined'
}

_log_level_values = {
    'critical':  CRITICAL,
    'error':     ERROR,
    'warning':   WARNING,
    'info':      INFO,
    'verbose':   VERBOSE,
    'debug':     DEBUG,
    'undefined': NOTSET,
    'notset':    NOTSET
}


def _check_level(level):
    if isinstance(level, numbers.Integral):
        ret = level
    elif isinstance(level, str):
        norm_level = level.lower()
        if norm_level not in _log_level_values:
            raise ValueError('logger level %s not available' % level)
        else:
            ret = _log_level_values[norm_level]
    else:
        raise TypeError('logger level %s not an int or a valid string' % level)

    return ret


# Here we want that all the handlers of Python's logging framework understand
# our log levels. For this reason we need to do two things:
#
# 1. Monkey-patch the `setLevel` method of `logging.Handler` with our method
#    that understands our levels.
# 2. We need a way to differentiate the patched handlers. For this reason, we
#    make the `logging.Handler` a pseudo-subclass of our custom `Handler` class,
#    which itself should be abstract and unable to be instantiated.

class Handler(abc.ABC):
    @abc.abstractmethod
    def __init__(self):
        pass


Handler.register(logging.Handler)


def set_handler_level(hdlr, level):
    hdlr.level = _check_level(level)


logging.Handler.setLevel = set_handler_level


class DynamicFileHandler(logging.FileHandler):
    """A file handler that allows its filename to be specified lazily using fields
    from a log record.
    """

    def __init__(self, name_pattern, mode='a', encoding=None):
        super().__init__(name_pattern, mode, encoding, delay=True)

        # Reset FileHandler's filename
        self.baseFilename = None
        self._filename_patt = name_pattern

    def emit(self, record):
        # Generate dynamically a filename from the record information
        if self.stream is None:
            try:
                self.baseFilename = self._filename_patt % record.__dict__
            except KeyError as e:
                raise LoggingError('unknown placeholder in '
                                   'filename pattern: %s' % e) from None

        super().emit(record)


def load_from_dict(logging_config, auto_logfile=None):
    if not isinstance(logging_config, collections.abc.Mapping):
        raise TypeError('logging configuration is not a dict')

    level = logging_config.get('level', 'info').lower()
    handlers_descr = logging_config.get('handlers', None)
    logger = Logger('reframe')
    logger.setLevel(_log_level_values[level])

    for handler in _extract_handlers(handlers_descr, auto_logfile):
        logger.addHandler(handler)

    return logger


def _convert_handler_syntax(handler_dict):
    handler_list = []
    for filename, handler_config in handler_dict.items():
        descr = handler_config
        new_keys = {}
        if filename == '&1':
            new_keys['type'] = 'stream'
            new_keys['stream'] = 'stdout'
        elif filename == '&2':
            new_keys['type'] = 'stream'
            new_keys['stream'] = 'stderr'
        else:
            new_keys['type'] = 'file'
            new_keys['name'] = filename

        descr.update(new_keys)
        handler_list.append(descr)

    return handler_list


def _create_file_handler(handler_config):
    try:
        filename = handler_config['name']
    except KeyError:
        raise ConfigError('no file specified for file handler:\n%s' %
                          pprint.pformat(handler_config)) from None

    timestamp = handler_config.get('timestamp', None)
    if timestamp:
        basename, ext = os.path.splitext(filename)
        filename = '%s_%s%s' % (
            basename, datetime.now().strftime(timestamp), ext)

    append = handler_config.get('append', False)
    return logging.handlers.RotatingFileHandler(filename,
                                                mode='a+' if append else 'w+')


def _create_dynfile_handler(handler_config):
    try:
        filename_patt = handler_config['name']
    except KeyError:
        raise ConfigError('no file specified for file handler:\n%s' %
                          pprint.pformat(handler_config)) from None

    append = handler_config.get('append', False)
    return DynamicFileHandler(filename_patt, mode='a+' if append else 'w+')


def _create_stream_handler(handler_config):
    stream = handler_config.get('stream', 'stdout')
    if stream == 'stdout':
        return logging.StreamHandler(stream=sys.stdout)
    elif stream == 'stderr':
        return logging.StreamHandler(stream=sys.stderr)
    else:
        raise ConfigError('unknown stream: %s' % stream)


def _create_graylog_handler(handler_config):
    try:
        import pygelf
    except ImportError:
        return None

    hostname = handler_config.get('hostname', None)
    port = handler_config.get('port', None)
    facility = handler_config.get('facility', None)
    return pygelf.GelfHttpHandler(host=hostname,
                                  port=port,
                                  facility=facility,
                                  debug=True,
                                  include_extra_fields=True)


def _extract_handlers(handlers_list, auto_logfile=None):
    # Check if we are using the old syntax
    if isinstance(handlers_list, collections.abc.Mapping):
        handlers_list = _convert_handler_syntax(handlers_list)
        sys.stderr.write(
            'WARNING: looks like you are using an old syntax for '
            'logging configuration; please update your syntax as follows:\n'
            '\nhandlers: %s\n' % pprint.pformat(handlers_list, indent=1))

    handlers = []
    if not handlers_list:
        raise ValueError('no handlers are defined for logging')

    for i, handler_config in enumerate(handlers_list):
        if not isinstance(handler_config, collections.abc.Mapping):
            raise TypeError('handler config at position %s '
                            'is not a dictionary' % i)

        try:
            handler_type = handler_config['type']
        except KeyError:
            raise ConfigError('no type specified for '
                              'handler at position %s' % i) from None

        if handler_type == 'file':
            hdlr = _create_file_handler(handler_config)
        elif handler_type == 'dynfile':
            hdlr = _create_dynfile_handler(handler_config)
        elif handler_type == 'stream':
            hdlr = _create_stream_handler(handler_config)
        elif handler_type == 'graylog':
            hdlr = _create_graylog_handler(handler_config)
            if hdlr is None:
                sys.stderr.write('WARNING: could not initialize the '
                                 'graylog handler; ignoring...\n')
                continue
        else:
            raise ConfigError('unknown handler type: %s' % handler_type)

        level = handler_config.get('level', 'debug').lower()
        fmt = handler_config.get('format', '%(message)s')
        datefmt = handler_config.get('datefmt', '%FT%T')
        hdlr.setFormatter(logging.Formatter(fmt=fmt, datefmt=datefmt))
        hdlr.setLevel(_check_level(level))
        handlers.append(hdlr)

    return handlers


class Logger(logging.Logger):
    def __init__(self, name, level=logging.NOTSET):
        # We will set the logger level ourselves so as to bypass the base
        # class' check
        super().__init__(name, logging.NOTSET)
        self.level = _check_level(level)

    def __repr__(self):
        return debug.repr(self)

    def setLevel(self, level):
        self.level = _check_level(level)

    def makeRecord(self, name, level, fn, lno, msg, args, exc_info,
                   func=None, extra=None, sinfo=None):
        record = super().makeRecord(name, level, fn, lno, msg, args, exc_info,
                                    func, extra, sinfo)
        try:
            # Fill in our name for the record
            record.levelname = _log_level_names[level]
        except KeyError:
            # Go with the default level name of Python logging
            pass

        return record

    # Override all the convenience logging functions, because we want to make
    # sure that they map to our level definitions

    def critical(self, msg, *args, **kwargs):
        return self.log(CRITICAL, msg, *args, **kwargs)

    def error(self, msg, *args, **kwargs):
        return self.log(ERROR, msg, *args, **kwargs)

    def warning(self, msg, *args, **kwargs):
        return self.log(WARNING, msg, *args, **kwargs)

    def info(self, msg, *args, **kwargs):
        return self.log(INFO, msg, *args, **kwargs)

    def verbose(self, message, *args, **kwargs):
        self.log(VERBOSE, message, *args, **kwargs)

    def debug(self, message, *args, **kwargs):
        self.log(DEBUG, message, *args, **kwargs)


class LoggerAdapter(logging.LoggerAdapter):
    def __init__(self, logger=None, check=None):
        super().__init__(
            logger,
            {
                'check_name': 'reframe',
                'check_jobid': '-1',
                'check_info': 'reframe',
                'check_system': None,
                'check_partition': None,
                'check_environ': None,
                'check_outputdir': None,
                'check_stagedir': None,
                'check_perf_logfile': None,
                'check_perf_var': None,
                'check_perf_value': None,
                'check_perf_ref': None,
                'check_perf_lower_thres': None,
                'check_perf_upper_thres': None,
                'data-version': reframe.VERSION,
                'version': reframe.VERSION,
            }
        )
        self.check = check

    def __repr__(self):
        return debug.repr(self)

    def setLevel(self, level):
        if self.logger:
            super().setLevel(level)

    def _update_check_extras(self):
        """Return a dictionary with all the check-specific information."""
        if self.check is None:
            return

        self.extra['check_name'] = self.check.name
        self.extra['check_info'] = self.check.info()
        self.extra['check_outputdir'] = self.check.outputdir
        self.extra['check_stagedir'] = self.check.stagedir
        if self.check.current_system:
            self.extra['check_system'] = self.check.current_system.name

        if self.check.current_partition:
            self.extra['check_partition'] = self.check.current_partition.name

        if self.check.current_environ:
            self.extra['check_environ'] = self.check.current_environ.name

        if self.check.job:
            self.extra['check_jobid'] = self.check.job.jobid

    def log_performance(self, level, tag, value, ref,
                        low_thres, upper_thres, msg=''):
        # Update the performance-relevant extras and log the message
        if self.check:
            self.extra['check_perf_logfile'] = self.check.perf_logfile

        self.extra['check_perf_var'] = tag
        self.extra['check_perf_value'] = value
        self.extra['check_perf_ref'] = ref
        self.extra['check_perf_lower_thres'] = low_thres
        self.extra['check_perf_upper_thres'] = upper_thres
        self.log(level, msg)

    def process(self, msg, kwargs):
        # Setup dynamic fields of the check
        self._update_check_extras()
        try:
            self.extra.update(kwargs['extra'])
        except KeyError:
            pass

        return super().process(msg, kwargs)

    # Override log() function to treat `None` loggers
    def log(self, level, msg, *args, **kwargs):
        if self.logger:
            super().log(level, msg, *args, **kwargs)

    def verbose(self, message, *args, **kwargs):
        self.log(VERBOSE, message, *args, **kwargs)


# A logger that doesn't log anything
null_logger = LoggerAdapter()

_logger = None
_perf_logger = None
_context_logger = null_logger


class logging_context:
    def __init__(self, check=None, level=DEBUG):
        global _context_logger

        self._level = level
        self._orig_logger = _context_logger
        if check is not None:
            _context_logger = LoggerAdapter(_logger, check)

    def __enter__(self):
        return _context_logger

    def __exit__(self, exc_type, exc_value, traceback):
        global _context_logger

        # Log any exceptions thrown with the current context logger
        if exc_type is not None:
            msg = 'caught {0}: {1}'
            exc_fullname = '%s.%s' % (exc_type.__module__, exc_type.__name__)
            getlogger().log(self._level, msg.format(exc_fullname, exc_value))

        # Restore context logger
        _context_logger = self._orig_logger


def configure_logging(loggin_config, perf_logging_config=None):
    global _logger, _perf_logger, _context_logger

    if loggin_config is None:
        _logger = None
        _context_logger = null_logger
        return

    _logger = load_from_dict(loggin_config)
    _context_logger = LoggerAdapter(_logger)
    if perf_logging_config:
        _perf_logger = load_from_dict(perf_logging_config)


def save_log_files(dest):
    os.makedirs(dest, exist_ok=True)
    for hdlr in _logger.handlers:
        if isinstance(hdlr, logging.FileHandler):
            shutil.copy(hdlr.baseFilename, dest, follow_symlinks=True)


def getlogger():
    return _context_logger


def getperflogger(check):
    return LoggerAdapter(_perf_logger, check)
