"""Command-Line Interface for filmalize.

This module contains the Click command definitions as well as helper functions.
It also contains classes used for the progress bars that are displayed to the
user once the transcoding has been started.

"""

import os
import time

import click
import progressbar
import blessed

from filmalize.errors import (ProbeError, ProgressFinishedError)
from filmalize.models import Container
from filmalize.menus import main_menu, display_container


# Allow help to be called with '-h' as well as the default '--help'.
CONTEXT_SETTINGS = dict(help_option_names=['-h', '--help'])


class Writer(object):
    """Writes messages to a specific line on the screen, defined at
    instantiation.

    Args:
        line (:obj:`int`): The line of the screen for this instance to write
            to.
        terminal (:obj:`blessed.terminal.Terminal`): Where to write.
        color (:obj:`str`, optional): The color to print in. Must conform to
            the blessed color function `format`_.

    Attributes:
        line (:obj:`int`): The line of the screen that this instance writes
            to.
        terminal (:obj:`blessed.terminal.Terminal`): Where to write.
        color (:obj:`str`): The color to print in.

    .. _format: http://blessed.readthedocs.io/en/latest/overview.html#colors
    """

    def __init__(self, line, terminal, color=None):

        self.line = line
        self.terminal = terminal
        self.color = color

    def write(self, message):
        """Write a message to the screen.

        The message is written to the :obj:`Writer.terminal`, on the
        :obj:`Writer.line`, and in :obj:`Writer.color`, if set.

        Args:
            message (:obj:`str`): The message to display

        """
        with self.terminal.location(x=0, y=self.line):
            if self.color:
                print(getattr(self.terminal, self.color)(message))
            else:
                print(message)

    @staticmethod
    def flush():
        """Pretend to flush as if :obj:`Writer` was a real file descriptor.

        :obj:`progressbar.bar.ProgressBar` objects expect to flush their file
        descriptors, but since we're actually printing to a
        :obj:`blessed.terminal.Terminal`, this method is needed to keep the
        progress bars happy.

        Returns:
            :obj:`bool`: True

        """
        return True


class ErrorWriter(object):
    """Write error messages in bright red to the bottom of a Terminal.

    Args:
        terminal (:obj:`blessed.terminal.Terminal`): Where to write.

    Attributes:
        terminal (:obj:`blessed.terminal.Terminal`): Where to write.
        line (:obj:`int`): The highest line to write to. Starts at the bottom
            of the screen and increments upward as additional messages are
            written.
        messages (:obj:`list` of :obj:`str`): The messages to write.

    """

    def __init__(self, terminal):

        self.terminal = terminal
        self.line = terminal.height - 1
        self.messages = []

    def write(self, message):
        """Write a message to the lowest line on the Terminal.

        As subsequent messages are written, earlier messages are moved upward.

        Args:
            message (:obj:`str`): The message to display.

        """

        self.messages.append(message)
        with self.terminal.location(x=0, y=self.line):
            for message in self.messages:
                print(self.terminal.red(message), self.terminal.clear_eol)
        self.line -= 1


def exclusive(ctx_params, exclusive_params, error_message):
    """Utility function for enforcing exclusivity between click options.

    Call at the top of a :obj:`click.group` or :obj:`click.command` definition.

    Args:
        ctx_params (:obj:`dict`): The context parameters to search.
        exclusive_params (:obj:`list` of :obj:`str`): Mutually exclusive
            parameters.
        error_message (:obj:`str`): The error message to display.

    Raises:
        click.UsageError: If more than one exclusive parameter is present in
            the context parameters.

    Examples::

        @click.command()
        @click.option('-s', '--song', default='')
        @click.option('-a', '--album', default='')
        def music(song, album):
            ctx_params = click.get_current_context().ctx_params
            exclusive_params = ['song', 'album']
            error_message = 'song and album are mutually exclusive'
            exclusive(ctx_params, exclusive_params, error_message)
            ...

        # You can also include parameters from multiple layers of a nested app.
        ...
        ctx_params = {**ctx.params, **ctx.parent.params}
        exclusive_params = ['a', 'b']
        error_message = 'command option b conflicts with parent option a'
        exclusive(ctx_params, exclusive_params, error_message)
        ...

    """

    if sum([1 if ctx_params[p] else 0 for p in exclusive_params]) > 1:
        raise click.UsageError(error_message)


def build_containers(file_list):
    """Utility function to build a list of :obj:`Container` instances given a
    list of filenames.

    Note:
        If a container fails to build as the result of a ffprobe error, that
        error is echoed after building has completed. If no containers are
        built, an empty list is returned.

    Args:
        file_list (:obj:`list` of :obj:`str`): File names to attempt to build
            into containers.

    Returns:
        :obj:`list` of :obj:`Container`: Succesfully built containers.

    """

    containers = []
    errors = []
    with click.progressbar(file_list, label='Scanning Files') as pr_bar:
        for file_name in pr_bar:
            try:
                containers.append(Container.from_file(file_name))
            except ProbeError as _e:
                errors.append(_e)
    for error in errors:
        click.secho('Warning: unable to process {}'
                    .format(error.file_name), fg='red')
        click.echo(error.message)
    return sorted(containers, key=lambda container: container.file_name)


@click.group(context_settings=CONTEXT_SETTINGS)
@click.option(
    '-f', '--file', help='Specify a file.',
    type=click.Path(exists=True, dir_okay=False, readable=True),
)
@click.option(
    '-d', '--directory', help='Specify a directory.',
    type=click.Path(exists=True, file_okay=False, readable=True)
)
@click.option('-r', '--recursive', is_flag=True, help='Operate recursively.')
@click.pass_context
def cli(ctx, file, directory, recursive):
    """A simple tool for converting video files.

    By default filmalize operates on all files in the current directory. If
    desired, you may specify an individual file or a different working
    directory. Directory operation may be recursive. A command is required.

    """

    exclusive(ctx.params, ['file', 'directory'],
              'a file may not be specified with a directory')
    exclusive(ctx.params, ['file', 'recursive'],
              'a file may not be specified with the recursive flag')

    ctx.obj = {}

    if file:
        ctx.obj['FILES'] = [file]
    else:
        directory = directory if directory else '.'
        if recursive:
            ctx.obj['FILES'] = sorted(
                [os.path.join(root, file)
                 for root, dirs, files in os.walk(directory)
                 for file in files]
            )
        else:
            ctx.obj['FILES'] = sorted(
                [dir_entry.path for dir_entry in os.scandir(directory)
                 if dir_entry.is_file()]
            )


@cli.command()
@click.pass_context
def display(ctx):
    """Display information about video file(s)"""

    for container in build_containers(ctx.obj['FILES']):
        display_container(container)


@cli.command()
@click.pass_context
def convert(ctx):
    """Convert video file(s)"""

    containers = build_containers(ctx.obj['FILES'])
    running = main_menu(containers)
    terminal = blessed.Terminal()
    err = ErrorWriter(terminal)

    padding = max([len(container.file_name) for container in running])
    for line_number, container in enumerate(running):
        label = '{name:{length}}'.format(name=container.file_name,
                                         length=padding)
        widgets = [label, ' | ', progressbar.Percentage(), ' ',
                   progressbar.Bar(), progressbar.ETA()]
        writer = Writer(line_number + 2, terminal, 'red_on_black')
        container.pr_bar = progressbar.ProgressBar(
            max_value=container.microseconds, widgets=widgets, fd=writer)

    writer = Writer(0, terminal, 'bold_blue_on_black')
    total_ms = sum([container.microseconds for container in running])
    widgets = [progressbar.Percentage(), ' ', progressbar.Bar(),
               ' ', progressbar.Timer(), ' | ', progressbar.ETA()]
    pr_bar = progressbar.ProgressBar(max_value=total_ms, widgets=widgets,
                                     fd=writer)

    with terminal.fullscreen():
        while running:
            total_progress = 0
            for container in running:
                try:
                    progress = container.progress
                    container.pr_bar.update(progress)
                except (ProgressFinishedError) as _e:
                    err.write('Warning: ffmpeg error while converting'
                              '{}'.format(container.file_name))
                    err.write(container.process.communicate()[1]
                              .strip(os.linesep))

                    running.remove(container)
                    progress = container.microseconds
                    container.pr_bar.finish()

                total_progress += progress

            pr_bar.update(total_progress)
            time.sleep(0.2)

    pr_bar.finish()
    click.clear()
    for message in err.messages:
        click.secho(message, fg='red', bg='black', )


if __name__ == '__main__':
    cli()
