"""Core classes for filmalize.

This module contains the classes that do most of the heavy lifting. It should
stand alone, and allow for other interfaces to be built using it. The main
class is the :obj:`Container`, which may be created manually, or with the
:obj:`classmethod` :obj:`Container.from_file` or :obj:`Container.from_dict`.

"""

import os
import datetime
import tempfile
import subprocess
import json
import pathlib

import chardet
import bitmath

import filmalize.defaults as defaults
from filmalize.errors import ProbeError, ProgressFinishedError


class EqualityMixin(object):
    """Mixin class that adds equality checking.

    Note:
        Equality checks are performed by checking the equality of the
        :obj:`object.__dict__` methods of the classes at issue.

        To exclude attributes from the comparison, add an attribute
        :obj:`equality_ignore` to the class. Populate this attribute with a
        :obj:`list` of :obj:`str` names of attributes to exclude.

    """

    def __eq__(self, other):
        if isinstance(other, self.__class__):
            diff = [other.__dict__[key] == value
                    for key, value in self.__dict__.items()
                    if key not in getattr(self, 'equality_ignore', [])]
            return sum(diff) == len(diff)
        else:
            return NotImplemented

    def __ne__(self, other):
        return not self == other


class ContainerLabel(EqualityMixin):
    """Labels for :obj:`Container` objects

    Note:
        The information stored here is simply for display and will not affect
        the output file.

    Args:
        title (:obj:`str`, optional): Container title.
        size (:obj:`float`, optional): Container file size in MiBs.
        bitrate (:obj:`float`, optional): Container overall bitrate in Mib/s.
        container_format (:obj:`str`, optional): Container file format.
        length (:obj:`datetime.timedelta`, optional): The duration of the file
            as a timedelta.

    Attributes:
        title (:obj:`str`): Container title.
        size (:obj:`float`): Container file size in MiBs.
        bitrate (:obj:`float`): Container overall bitrate in Mib/s.
        container_format (:obj:`str`): Container file format.
        length (:obj:`datetime.timedelta`): The duration of the file as a
            timedelta.


    """

    def __init__(self, title=None, size=None, bitrate=None,
                 container_format=None, length=None):

        self.title = title if title else ''
        self.size = size if size else ''
        self.bitrate = bitrate if bitrate else ''
        self.container_format = container_format if container_format else ''
        self.length = length if length else ''

    @classmethod
    def from_dict(cls, info):
        """Build a :obj:`ContainerLabel` instance from a dictionary.

        Args:
            info (:obj:`dict`): Container information in dictionary format
                structured in the manner of ffprobe json output.

        Returns:
            Instance populated wtih data from the given dictionary.

        """
        title = info.get('format', {}).get('tags', {}).get('title', '')
        f_bytes = int(info.get('format', {}).get('size', 0))
        size = round(bitmath.MiB(bytes=f_bytes).value, 2) if f_bytes else ''
        bits = int(info.get('format', {}).get('bit_rate', 0))
        bitrate = round(bitmath.Mib(bits=bits).value, 2) if bits else ''
        container_format = info.get('format', {}).get('format_long_name', '')
        duration = float(info.get('format', {}).get('duration', 0))
        length = datetime.timedelta(0, round(duration)) if duration else ''

        return cls(title=title, size=size, bitrate=bitrate,
                   container_format=container_format, length=length)


class Container(EqualityMixin):
    """Multimedia container file object.

    Args:
        file_name (:obj:`str`): The name of the input file.
        duration (:obj:`float`): The duration of the streams in the container
            in seconds.
        streams (:obj:`list` of :obj:`Stream`): The mutimedia streams in this
            :obj:`Container`.
        subtitle_files (:obj:`list` of :obj:`SubtitleFile`, optional): Subtitle
            files to add to the output file.
        selected (:obj:`list` of :obj:`int`, optional): Indexes of the
            :obj:`Stream` instances to include in the output file. If not
            specified, the first audio and video stream will be selected.
        output_name (:obj:`str`, optional): Output filename. If not specified,
            the output filename will be set to be the same as the input file,
            but with the extension replaced with the proper one for the
            output format.
        labels (:obj:`ContainerLabel`, optional): Informational metadata about
            the input file.

    Attributes:
        file_name (:obj:`str`): The name of the input file.
        duration (:obj:`float`): The duration of the streams in the container
            in seconds.
        streams (:obj:`list` of :obj:`Stream`): The mutimedia streams in this
            :obj:`Container`.
        subtitle_files (:obj:`list` of :obj:`SubtitleFile`): Subtitle files to
            add to the output file.
        output_name (:obj:`str`): Output filename.
        labels (:obj:`ContainerLabel`): Informational metadata about the input
            file.
        microseconds (:obj:`int`): The duration of the file expressed in
            microseconds.
        temp_file (:obj:`tempfile.NamedTemporaryFile`): The temporary file for
            ffmpeg to write status information to.
        process (:obj:`subprocess.Popen`): The subprocess in which ffmpeg
            processes the file.
        equality_ignore (:obj:`list` of :obj:`string`): Attributes to ignore
            when checking for equality of Container instances.

    """

    def __init__(self, file_name, duration, streams, subtitle_files=None,
                 selected=None, output_name=None, labels=None):

        self.file_name = file_name
        self.duration = duration
        self.streams = streams
        self.subtitle_files = subtitle_files if subtitle_files else []
        self.output_name = output_name if output_name else self.default_name
        self._selected = []
        self.selected = selected if selected else self.default_streams
        self.labels = labels if labels else ContainerLabel()

        self.microseconds = int(duration * 1000000)
        self.temp_file = tempfile.NamedTemporaryFile(delete=False)
        self.process = None
        self.equality_ignore = ['temp_file', 'process']

    @classmethod
    def from_file(cls, file_name):
        """Build a :obj:`Container` from a given multimedia file.

        Attempt to probe the file with ffprobe. If the probe is succesful,
        finish instatiation by passing the results to
        :obj:`Container.from_dict`.

        Args:
            file_name (:obj:`str`): The file (a multimedia container) to
                represent.

        Returns:
            :obj:`Container`: Instance representing the given file.

        Raises:
            :obj:`ProbeError`: If ffprobe is unable to successfully probe the
                file.

        """

        probe_response = subprocess.run(
            [defaults.FFPROBE, '-v', 'error', '-show_format',
             '-show_streams', '-of', 'json', file_name],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        if probe_response.returncode:
            raise ProbeError(file_name, probe_response.stderr.decode('utf-8')
                             .strip(os.linesep))

        info = json.loads(probe_response.stdout)
        return cls.from_dict(info)

    @classmethod
    def from_dict(cls, info):
        """Build a :obj:`Container` from a given dictionary.

        Args:
            info (:obj:`dict`): Container information in dictionary format
                structured in the manner of ffprobe json output.

        Returns:
            :obj:`Container`: Instance representing the given info.

        Raises:
            :obj:`ProbeError`: If the info does not contain a 'duraton' tag.

        """

        file_name = info['format']['filename']
        duration = float(info.get('format', {}).get('duration', 0))
        if not duration:
            raise ProbeError(file_name, 'File has no duration tag.')

        streams = [Stream.from_dict(stream) for stream in info['streams']]
        labels = ContainerLabel.from_dict(info)

        return cls(file_name=file_name, duration=duration, streams=streams,
                   labels=labels)

    @property
    def default_name(self):
        """:obj:`str`: The input filename reformatted with the selected output
        file extension."""

        return pathlib.PurePath(self.file_name).stem + defaults.ENDING

    @property
    def default_streams(self):
        """:obj:`list` of :obj:`int`: The indexes of the first video and audio
        :obj:`Stream`."""

        streams = []
        audio, video = None, None
        for stream in self.streams:
            if not audio and stream.type == 'audio':
                audio = True
                streams.append(stream.index)
            elif not video and stream.type == 'video':
                video = True
                streams.append(stream.index)

        return streams

    @property
    def selected(self):
        """:obj:`list` of :obj:`int`: Indexes of the :obj:`Stream` instances to
        include in the output file.

        Note:
            At this time filmalize can only output audio, video, and subtitle
            streams.

        Raises:
            :obj:`ValueError`: If list contains an index that does
                not correspond to any :obj:`Stream` in :obj:`Container.streams`
                or if :obj:`Stream.type` is unsupported.

        """

        return self._selected

    @selected.setter
    def selected(self, index_list):

        streams = self.streams_dict
        for index in index_list:
            if index not in streams.keys():
                raise ValueError('This contaner does not contain a stream '
                                 'with index {}'.format(index))
            if streams[index].type not in ['audio', 'video', 'subtitle']:
                raise ValueError('filmalize cannot output streams of type {}'
                                 .format(streams[index].type))

        self._selected = sorted(index_list)

    @property
    def streams_dict(self):
        """:obj:`dict` of {:obj:`int`: :obj:`Stream`}: The :obj:`Stream`
        instances in :obj:`Container.streams` keyed by their indexes."""
        return {stream.index: stream for stream in self.streams}

    @property
    def progress(self):
        """:obj:`int`: The number of microseconds that ffmpeg has processed.

        Raises:
            :obj:`ProgressFinishedError`: If the subprocess is not running
                (either finished or errored out).

        """
        if not self.process:
            return 0
        elif self.process.poll() is not None:
            raise ProgressFinishedError
        else:
            try:
                self.temp_file.seek(-512, os.SEEK_END)
            except OSError:
                self.temp_file.seek(0)
            binary_lines = self.temp_file.readlines(512)
            line_list = [_l.decode().strip(os.linesep) for _l in binary_lines]

            microsec = 0
            for line in reversed(line_list):
                if line.split('=')[0] == 'out_time_ms':
                    microsec = int(line.split('=')[1])
                    break

            return microsec

    def add_subtitle_file(self, file_name, encoding=None):
        """Add an external subtitle file. Optionally set a custom file
        encoding.

        Args:
            file_name (:obj:`str`): The name of the subtitle file.
            encoding (:obj:`str`, optional): The encoding of the subtitle file.

        """

        self.subtitle_files.append(SubtitleFile(file_name, encoding))

    def convert(self):
        """Start the conversion of this container in a subprocess."""

        self.process = subprocess.Popen(
            self.build_command(),
            stderr=subprocess.PIPE,
            universal_newlines=True
        )

    def build_command(self):
        """Build the ffmpeg command to process this container.

        Generate appropriate ffmpeg options to process the streams selected in
        :obj:`self.selected`.

        Returns:
            :obj:`list` of :obj:`str`: The ffmpeg command and options to
            execute.

        """

        command = [defaults.FFMPEG, '-nostdin', '-progress',
                   self.temp_file.name, '-v', 'error', '-y', '-i',
                   self.file_name]
        for subtitle in self.subtitle_files:
            command.extend(['-sub_charenc', subtitle.encoding, '-i',
                            subtitle.file_name])
        for stream in self.selected:
            command.extend(['-map', '0:{}'.format(stream)])
        for index, _ in enumerate(self.subtitle_files):
            command.extend(['-map', '{}:0'.format(index + 1)])
        stream_number = {'video': 0, 'audio': 0, 'subtitle': 0}
        output_streams = [s for s in self.streams if s.index in self.selected]
        for stream in output_streams:
            command.extend(stream.build_options(stream_number[stream.type]))
            stream_number[stream.type] += 1
        for subtitle in self.subtitle_files:
            command.extend(['-c:s:{}'.format(stream_number['subtitle'])])
            command.extend(subtitle.options)
            stream_number['subtitle'] += 1
        command.extend([os.path.join(os.path.dirname(self.file_name),
                                     self.output_name)])

        return command


class StreamLabel(EqualityMixin):
    """Labels for :obj:`Stream` objects.

    Note:
        The information stored here is simply for display and (with one
        exception) will not affect the output file.

        For audio streams, if this stream cannot be copied and must be
        transcoded, if there is not a :obj:`Stream.custom_bitrate` set, and if
        there is a value stored in :obj:`StreamLabel.bitrate`, that value
        will be chosen by default as the output stream target bitrate.

    Args:
        title (:obj:`str`): Stream title.
        bitrate (:obj:`float`): Stream bitrate in Mib/s for video streams or
            Kib/s for audio streams.
        resolution (:obj:`str`): Stream resolution.
        language (:obj:`str`): Language name or abbreviation.
        channels (:obj:`str`): Audio channel information (stereo, 5.1, etc.).
        default (:obj:`bool`): True if this stream is the default stream of its
            type, else False.

    Attributes:
        title (:obj:`str`): Stream title.
        bitrate (:obj:`float`): Stream bitrate in Mib/s for video streams or
            Kib/s for audio streams.
        resolution (:obj:`str`): Stream resolution.
        language (:obj:`str`): Language name or abbreviation.
        channels (:obj:`str`): Audio channel information (stereo, 5.1, etc.).

    """

    def __init__(self, title=None, bitrate=None, resolution=None,
                 language=None, channels=None, default=None):

        self.title = title if title else ''
        self.bitrate = bitrate if bitrate else ''
        self.resolution = resolution if resolution else ''
        self.language = language if language else ''
        self.channels = channels if channels else ''
        self._default = 'default' if default else ''

    @classmethod
    def from_dict(cls, info):
        """Build a :obj:`StreamLabel` instance from a dictionary.

        Args:
            info (:obj:`dict`): Stream information in dictionary format
                structured in the manner of ffprobe json output.

        Returns:
            Instance populated with the data from the given directory.

        """

        stream_type = info['codec_type']
        title = info.get('tags', {}).get('title', '')
        bits = int(info.get('bit_rate', 0))
        if stream_type == 'video' and bits:
            bitrate = round(bitmath.Mib(bits=bits).value, 2)
        elif stream_type == 'audio' and bits:
            bitrate = round(bitmath.Kib(bits=bits).value)
        else:
            bitrate = ''
        height = str(info.get('height', info.get('coded_height', '')))
        width = str(info.get('width', info.get('coded_width', '')))
        resolution = width + 'x' + height if height and width else ''
        language = info.get('tags', {}).get('language', '')
        channels = info.get('channel_layout', '')
        default = bool(info.get('disposition', {}).get('default'))

        return cls(title=title, bitrate=bitrate, resolution=resolution,
                   language=language, channels=channels, default=default)

    @property
    def default(self):
        """:obj:`str`: 'default' if this stream is the default stream of its
        type, else ''.

        Args:
            is_default (bool): True if this stream is the default stream of its
                type, else False.

        """
        return self._default

    @default.setter
    def default(self, is_default):
        self._default = 'default' if is_default else ''


class Stream(EqualityMixin):
    """Multimedia stream object.

    Note:
        At this time, :obj:`Stream` instances will only be included in the
        output file if they have type of 'audio', 'video', or 'subtitle'.

    Args:
        index (:obj:`int`): The stream index.
        stream_type (:obj:`str`): The multimedia type of the stream as reported
            by ffprobe.
        codec (:obj:`str`): The codec with which the stream is encoded as
            as reported by ffprobe.
        custom_crf (:obj:`int`, optional): Video stream Constant Rate Factor.
            If specified, this stream will be transcoded using this crf even
            if the input stream is suitable for copying to the output file.
        custom_bitrate (:obj:`float`, optional): Audio stream ouput bitrate in
            Kib/s. If specified, this audio stream will be transcoded using
            this as the target bitrate even if the input stream is suitable for
            copying and even if there is a bitrate set in the
            :obj:`StreamLabel`.
        labels (:obj:`StreamLabel`, optional): Informational metadata about the
            input stream.

    Attributes:
        index (:obj:`int`): The stream index.
        stream_type (:obj:`str`): The multimedia type of the stream as reported
            by ffprobe.
        codec (:obj:`str`): The codec with which the stream is encoded as
            as reported by ffprobe.
        custom_crf (:obj:`int`): Video stream Constant Rate Factor.
            If set, this stream will be transcoded using this crf even
            if the input stream is suitable for copying to the output file.
        custom_bitrate (:obj:`float`): Audio stream ouput bitrate in Kib/s. If
            set, this audio stream will be transcoded using this as
            the target bitrate even if the input stream is suitable for
            copying and even if there is a bitrate set in the
            :obj:`StreamLabel`.
        labels (:obj:`StreamLabel`): Informational metadata about the
            input stream.

    """

    def __init__(self, index, stream_type, codec, custom_crf=None,
                 custom_bitrate=None, labels=None):

        self.index = index
        self.type = stream_type
        self.codec = codec
        self.custom_crf = custom_crf if custom_crf else None
        self.custom_bitrate = custom_bitrate if custom_bitrate else None
        self.labels = labels if labels else StreamLabel()

        self.option_summary = None

    @classmethod
    def from_dict(cls, info):
        """Build a :obj:`Stream` instance from a dictionary.

            Args:
                info (:obj:`dict`): Stream information in dictionary
                    format structured in the manner of ffprobe json output.

            Returns:
                :obj:`Stream`: Instance populated with data from the given
                dictionary.

        """

        index = info['index']
        stream_type = info['codec_type']
        codec = info.get('codec_name', '')
        labels = StreamLabel.from_dict(info)

        return cls(index=index, stream_type=stream_type, codec=codec,
                   labels=labels)

    def build_options(self, number=0):
        """Generate ffmpeg codec/bitrate options for this :obj:`Stream`.

        The options generated will use custom values for video CRF or audio
        bitrate, if specified, or the default values. The option_summary is
        updated to reflect the selected options.

        Args:
            number (:obj:`int`, optional): The number of Streams of this type
                that have been added to the command.

        Returns:
            :obj:`list` of :obj:`str`: The ffmpeg options for this Stream.

        """

        options = []
        if self.type == 'video':
            options.extend(['-c:v:{}'.format(number)])
            if self.custom_crf or self.codec != defaults.C_VIDEO:
                crf = (self.custom_crf if self.custom_crf
                       else defaults.CRF)
                options.extend(['libx264', '-preset', defaults.PRESET, '-crf',
                                str(crf), '-pix_fmt', 'yuv420p'])
                self.option_summary = ('transcode -> {}, crf={}'
                                       .format(defaults.C_VIDEO, crf))
            else:
                options.extend(['copy'])
                self.option_summary = 'copy'
        elif self.type == 'audio':
            options.extend(['-c:a:{}'.format(number)])
            if self.custom_bitrate or self.codec != defaults.C_AUDIO:
                bitrate = (self.custom_bitrate if self.custom_bitrate
                           else self.labels.bitrate if self.labels.bitrate
                           else defaults.BITRATE)
                options.extend([defaults.C_AUDIO, '-b:a:{}'.format(number),
                                '{}k'.format(bitrate)])
                self.option_summary = ('transcode -> {}, bitrate={}Kib/s'
                                       .format(defaults.C_AUDIO, bitrate))
            else:
                options.extend(['copy'])
                self.option_summary = 'copy'
        elif self.type == 'subtitle':
            options.extend(['-c:s:{}'.format(number)])
            if self.codec == defaults.C_SUBS:
                options.extend(['copy'])
                self.option_summary = 'copy'
            else:
                options.extend([defaults.C_SUBS])
                self.option_summary = 'transcode -> {}'.format(defaults.C_SUBS)

        return options


class SubtitleFile(EqualityMixin):
    """Subtitle file object.

    Args:
        file_name (:obj:`str`): The subtitle file to represent.
        encoding (:obj:`str`, optional): The file encoding of the subtitle
            file.

    Attributes:
        file_name (:obj:`str`): The subtitle file represented.
        encoding (:obj:`str`): The file encoding of the subtitle file.


    """

    def __init__(self, file_name, encoding=None):

        self.file_name = file_name
        self.encoding = encoding if encoding else self.guess_encoding()
        self.options = [defaults.C_SUBS]
        self.option_summary = 'transcode -> {}'.format(defaults.C_SUBS)

    def guess_encoding(self):
        """Guess the encoding of the subtitle file.

        Open the given file, read a line, and pass that line to
        :obj:`chardet.detect` to produce a guess at the file's encoding.

        Returns:
            str: The best guess for the subtitle file encoding.

        """
        with open(self.file_name, mode='rb') as _file:
            lines = [_file.readline() for _ in range(10)]
        detected = chardet.detect(b''.join(lines))
        return detected['encoding']
