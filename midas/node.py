#!/usr/bin/env python3

# This file is part of the MIDAS system.
# Copyright 2014
# Andreas Henelius <andreas.henelius@ttl.fi>,
# Jari Torniainen <jari.torniainen@ttl.fi>
# Finnish Institute of Occupational Health
#
# This code is released under the MIT License
# http://opensource.org/licenses/mit-license.php
#
# Please see the file LICENSE for details.

import random
import zmq
import multiprocessing as mp
import time
import sys
from . import utilities as mu
from . import pylsl_python3 as lsl


class BaseNode(object):

    """ Simple MIDAS base node class. """

    def __init__(self,
                 config=None,
                 nodename="basenode",
                 nodetype='',
                 nodeid="00",
                 nodedesc="base node",
                 primary_node=True,
                 ip=None,
                 port_frontend=5001,
                 port_backend=5002,
                 port_publisher='',
                 n_workers=5,
                 lsl_stream_name=None,
                 n_channels=None,
                 channel_names=[],
                 channel_descriptions=None,
                 sampling_rate=None,
                 buffer_size_s=30,
                 run_publisher=False,
                 secondary_data=False,
                 n_channels_secondary=0,
                 buffer_size_secondary=0,
                 channel_names_secondary=[],
                 channel_descriptions_secondary=None,
                 default_channel=''):
        """ Initializes a basic MIDAS node class. Arguments can be passed either as
            config dict or specified spearately. If argumets are passed via
            both methods the ini-file will overwrite manually specified
            arguments.
        """

        # Parse information from a dictionary (from an ini-file), if provided
        if config:
            # general node properties
            if 'nodename' in config:
                nodename = config['nodename']

            if 'nodetype' in config:
                nodetype = config['nodetype']

            if 'nodeid' in config:
                nodeid = config['nodeid']

            if 'nodedesc' in config:
                nodedesc = config['nodedesc']

            if 'ip' in config:
                ip = config['ip'].lower().strip()

            if 'primary_node' in config:
                primary_node = mu.str2bool(config['primary_node'])

            if 'port_frontend' in config:
                port_frontend = int(config['port_frontend'])

            if 'port_backend' in config:
                port_backend = int(config['port_backend'])

            if 'port_publisher' in config:
                port_publisher = int(config['port_publisher'])

            if 'run_publisher' in config:
                run_publisher = mu.str2bool(config['run_publisher'])

            if 'n_workers' in config:
                n_workers = int(config['n_workers'])

            # data stream properties
            if 'lsl_stream_name' in config:
                lsl_stream_name = config['lsl_stream_name']

            if 'n_channels' in config:
                n_channels = int(config['n_channels'])

            if 'channel_names' in config:
                channel_names = mu.listify(config, 'channel_names')

            if 'channel_descriptions' in config:
                channel_descriptions = mu.listify(
                    config,
                    'channel_descriptions')

            if 'sampling_rate' in config:
                sampling_rate = int(config['sampling_rate'])

            if 'buffer_size_s' in config:
                buffer_size_s = float(config['buffer_size_s'])

            # secondary channels (channels generated by the node)
            if 'secondary_data' in config:
                secondary_data = config['secondary_data']

            if 'default_channel' in config:
                default_channel = config['default_channel']

            if 'n_channels_secondary' in config:
                n_channels_secondary = int(config['n_channels_secondary'])

            if 'buffer_size_secondary' in config:
                buffer_size_secondary = int(config['buffer_size_secondary'])

            if 'channel_names_secondary' in config:
                channel_names_secondary = mu.listify(
                    config,
                    'channel_names_secondary')

            if 'channel_descriptions_secondary' in config:
                channel_descriptions_secondary = mu.listify(
                    config,
                    'channel_descriptions_secondary')

        # general node properties
        self.nodename = nodename
        self.nodetype = nodetype
        self.nodeid = nodeid
        self.nodedesc = nodedesc
        self.primary_node = primary_node
        self.port_frontend = port_frontend
        self.port_backend = port_backend
        self.port_publisher = port_publisher
        self.run_publisher = run_publisher
        self.n_workers = n_workers

        # Automatically determine the IP of the node unless set in the node
        # configuration
        if (ip is None) or (ip == 'auto'):
            ip = mu.get_ip()
        elif ip is 'localhost':
            ip = '127.0.0.1'
        self.ip = ip

        self.url_frontend = mu.make_url(self.ip, self.port_frontend)
        self.url_backend = mu.make_url('127.0.0.1', self.port_backend)

        # publisher settings
        self.topic_list = {}

        if self.run_publisher:
            self.url_publisher = "tcp://" + \
                str(self.ip) + ":" + str(self.port_publisher)
            self.message_queue = mp.Queue(10)
        else:
            self.url_publisher = ''

        # primary channels and data stream properties
        if self.primary_node:
            self.lsl_stream_name = lsl_stream_name
            self.n_channels = n_channels
            self.channel_names = channel_names
            self.channel_descriptions = channel_descriptions
            self.sampling_rate = sampling_rate
            self.buffer_size_s = buffer_size_s
            self.buffer_size = int(self.buffer_size_s * self.sampling_rate)

        # secondary channels
        self.secondary_data = secondary_data
        self.default_channel = default_channel
        self.n_channels_secondary = n_channels_secondary
        self.buffer_size_secondary = [
            buffer_size_secondary]*self.n_channels_secondary
        self.channel_names_secondary = channel_names_secondary
        self.channel_descriptions_secondary = channel_descriptions_secondary

        if self.channel_descriptions is None:
            self.channel_descriptions = [''] * self.n_channels

        if self.channel_descriptions_secondary is None:
            self.channel_descriptions_secondary = [
                ''] * self.n_channels_secondary

        # ------------------------------
        # State variables:
        #    run_state      : poison pill to control processes
        #    wptr   : the current index being written to in the
        #                     circular buffer (channel_data)
        #    buffer_full    : has the circular buffer been full or not
        #    lock_primary   : lock for channel_data (primary data)
        #    lock_secondary : lock for the secondary channel_data
        # ------------------------------
        self.run_state = mp.Value('i', 0)

        self.wptr = mp.Value('i', 0)
        self.buffer_full = mp.Value('i', 0)

        self.lock_primary = mp.Lock()
        self.lock_secondary = []
        for i in range(self.n_channels_secondary):
            self.lock_secondary.append(mp.Lock())

        # ------------------------------
        # Data containers
        # ------------------------------
        # Preallocate primary buffers
        if self.primary_node:
            self.channel_data = [0] * self.n_channels

            for i in range(self.n_channels):
                self.channel_data[i] = mp.Array('d', [0] * self.buffer_size)

            self.time_array = mp.Array('d', [0] * self.buffer_size)
            self.last_time = mp.Array('d', [0])
        else:
            self.channel_data = []
            self.time_array = []
            self.last_time = []

        # Preallocate secondary buffers
        if self.secondary_data:
            self.channel_data_secondary = [0] * self.n_channels_secondary
            self.time_array_secondary = [0] * self.n_channels_secondary
            self.last_time_secondary = mp.Array(
                'd',
                [0] *
                self.n_channels_secondary)

            for i in range(self.n_channels_secondary):
                self.channel_data_secondary[i] = mp.Array(
                    'd',
                    [0] *
                    self.buffer_size_secondary[i])
                self.time_array_secondary[i] = mp.Array(
                    'd',
                    [0] *
                    self.buffer_size_secondary[i])

            self.wptr_secondary = mp.Array(
                'i',
                [0] *
                self.n_channels_secondary)
            self.buffer_full_secondary = mp.Array(
                'i',
                [0] *
                self.n_channels_secondary)

        # ------------------------------
        # Empty containers for functions
        # ------------------------------
        self.metric_names = []
        self.metric_descriptions = []
        self.metric_pointers = []

        # ------------------------------
        # Empty container for processes
        # ------------------------------
        self.process_list = []

        # ------------------------------
        # Empty container for metric functions
        # ------------------------------
        self.metric_functions = []

    # --------------------------------------------------------------------------
    # Function for getting the next sample from the nodes channel data buffer
    # --------------------------------------------------------------------------
    def get_sample(self):
        """ Return the next sample from the channel_data circular buffer. """

        # current write pointer
        wpc = self.wptr.value

        while self.wptr.value == wpc:
            pass

        return [self.channel_data[i][wpc] for i in range(self.n_channels)]

    # --------------------------------------------------------------------------
    # Receiver (receives data and stores the data in a circular buffer)
    # --------------------------------------------------------------------------
    def receiver(self):
        """ Receive data from an LSL stream and store it in a circular
        buffer.
        """

        streams = []

        while not streams:
            print("Trying to connect to the stream: " + self.lsl_stream_name)
            streams = lsl.resolve_byprop(
                'name',
                self.lsl_stream_name,
                timeout=10)
            if not streams:
                print("\tStream not found, re-trying...")

        inlet = lsl.StreamInlet(streams[0], max_buflen=1)
        print("\tDone")

        i = 0
        self.last_time.value = 0  # init the last_time value
        while self.run_state.value:
            x, t = inlet.pull_sample()

            self.lock_primary.acquire()  # LOCK-ON

            for k in range(self.n_channels):
                self.channel_data[k][self.wptr.value] = x[k]

            if t is None:
                t = self.last_time.value + self.sampling_rate

            self.time_array[self.wptr.value] = t
            self.last_time.value = t

            i += 1
            self.wptr.value = i % self.buffer_size
            self.lock_primary.release()  # LOCK-OFF

            # is the buffer full
            if (0 == self.buffer_full.value) and (i >= self.buffer_size):
                self.buffer_full.value = 1

    # --------------------------------------------------------------------------
    # Publish messages that are placed in the message queue
    # --------------------------------------------------------------------------
    def publisher(self):
        """ Publish data using ZeroMQ.

            A message to be published is placed in the node's message queue
            (self.message_queue), from which this functions gets() the next
            message and publishes it using the node's publisher.
        """

        context = zmq.Context()
        socket = context.socket(zmq.PUB)
        socket.connect(self.url_publisher)

        while self.run_state.value:
            if not self.message_queue.empty():
                socket.send_string(
                    "%s;%s" %
                    (self.nodename, self.message_queue.get()))
            time.sleep(0.0001)

    # --------------------------------------------------------------------------
    # Respond to queries over ZeroMQ
    # --------------------------------------------------------------------------
    def responder(self, responder_id):
        """ Respond to queries over ZeroMQ.

            The responder listens to messages over ZeroMQ and handles messages
            following the MIDAS Messaging Protocol. The messages can be queries
            of metrics, data, or commands regarding, e.g., the state of the
            node.
        """

        context = zmq.Context()
        socket = context.socket(zmq.REQ)
        socket.connect(self.url_backend)
        socket.send(b"READY")

        print('Started new responder.\tID: ' + str(responder_id))

        while self.run_state.value:
            try:
                message = mu.midas_receive_message(socket)

                if message['type'] == 'metric':
                    return_value = self.analyze_metric(
                        message['parameters'],
                        message['timewindow'])

                elif message['type'] == 'data':
                    return_value = self.get_data(
                        message['parameters'],
                        message['timewindow'])

                elif message['type'] == 'command':
                    return_value = self.analyze_command(message['command'])

                else:
                    return_value = {"error": "not recognized"}

                mu.midas_send_reply(socket, message['address'], return_value)

            except zmq.ContextTerminated:
                return

    # --------------------------------------------------------------------------

    def data_snapshot(self, timewindow):
        """ Take a snapshot of the node's primary data using locks. """

        channel_data_copy = [0] * self.n_channels

        self.lock_primary.acquire()

        time_array_copy = self.time_array[:]
        bf_copy = self.buffer_full.value
        wp_copy = self.wptr.value

        for i in range(self.n_channels):
            channel_data_copy[i] = self.channel_data[i][:]

        self.lock_primary.release()
        # create index vector to unwrap circular buffer
        ind = mu.get_index_vector(self.buffer_size, bf_copy, wp_copy)

        # unwrap the time vector
        time_array_copy = [time_array_copy[j] for j in ind]
        time_array_copy = [abs(i - time_array_copy[-1])
                           for i in time_array_copy]

        # find start index of data
        timewindow[1] = timewindow[0] - timewindow[1]
        index_start, index_stop = mu.find_range(time_array_copy, timewindow)
        time_array_copy = time_array_copy[index_start: index_stop]

        # unwrap the data
        for i in range(self.n_channels):
            channel_data_copy[i] = [
                channel_data_copy[i][j] for j in ind][
                index_start:index_stop]

        return channel_data_copy, time_array_copy

    # --------------------------------------------------------------------------

    def data_snapshot_secondary(self, timewindow):
        """ Take a snapshot of the node's secondary data using locks. """

        # empty containers for copies of data
        channel_data_copy = [0] * self.n_channels_secondary
        time_array_copy = [0] * self.n_channels_secondary

        # make copies
        [lock.acquire() for lock in self.lock_secondary]

        for i in range(self.n_channels_secondary):
            channel_data_copy[i] = self.channel_data_secondary[i][:]

        for i in range(self.n_channels_secondary):
            time_array_copy[i] = self.time_array_secondary[i][:]

        wp_copy = self.wptr_secondary[:]
        bf_copy = self.buffer_full_secondary[:]

        [lock.release() for lock in self.lock_secondary]

        for i in range(self.n_channels_secondary):
            # create index vector to unwrap circular buffer
            ind = mu.get_index_vector(
                self.buffer_size_secondary[i],
                bf_copy[i],
                wp_copy[i])

            # unwrap the time vector
            time_array_copy[i] = [time_array_copy[i][j] for j in ind]
            time_array_copy[i] = [abs(j - time_array_copy[i][-1])
                                  for j in time_array_copy[i]]

            # find start index of data
            # timewindow[1] = timewindow[0] - timewindow[1]
            index_start, index_stop = mu.find_range(
                time_array_copy[i], timewindow)
            time_array_copy[i] = time_array_copy[i][index_start:index_stop]

            # unwrap the data
            channel_data_copy[i] = [
                channel_data_copy[i][j] for j in ind][
                index_start:index_stop]

        return channel_data_copy, time_array_copy

    def push_sample_secondary(self, ch, timep, value, use_lock=True):
        """ Push a new sample into a secondary data buffer.

        Args:
               ch: <int>    secondary data channel index
            timep: <float>  time stamp of new sample
            value: <float>  value of new sample
        """
        if use_lock:
            self.lock_secondary[ch].acquire()

        self.channel_data_secondary[ch][self.wptr_secondary[ch]] = value
        self.time_array_secondary[ch][self.wptr_secondary[ch]] = timep
        self.wptr_secondary[ch] += 1

        if ((0 == self.buffer_full_secondary[ch]) and
                (self.wptr_secondary[ch] >= self.buffer_size_secondary[ch])):
            self.buffer_full_secondary[ch] = 1

        self.wptr_secondary[ch] = (self.wptr_secondary[ch] %
                                           self.buffer_size_secondary[ch])
        if use_lock:
            self.lock_secondary[ch].release()

        # check if we need to flip buffer_full flag

    def push_chunk_secondary(self, ch, timeps, values):
        """ Push a chunk of new samples into a secondary data buffer.

        Args:
                ch: <int>   secondary data channel index
            timeps: <list>  list of time stamps for new values
            values: <list>  list of new values
        """

        self.lock_secondary[ch].acquire()
        for t, v in zip(timeps, values):
            self.push_sample_secondary(ch, t, v, use_lock=False)
        self.lock_secondary[ch].release()

    # --------------------------------------------------------------------------
    # Return data
    # --------------------------------------------------------------------------
    def get_data(self, channel_names, timewindow):
        """ Return raw data (primary data) or refned secondary data.

        Args:
             channel_names : vector with channel names as strings
             timewindow    : a two-element array specifying the time window.

        Returns:
             A dictionary with the channel names as keys and the data as values.
        """

        if self.primary_node:
            channel_data, time_array = self.data_snapshot(timewindow)

        if self.secondary_data:
            channel_data_secondary, time_array_secondary = self.data_snapshot_secondary(
                timewindow)

        results = {}

        for cn in channel_names:
            if cn in self.channel_names:
                channel_index = mu.get_channel_index(self.channel_names, cn)

                results[cn] = {'data': channel_data[channel_index],
                               'time': time_array}

            if cn in self.channel_names_secondary:
                channel_index = mu.get_channel_index(
                    self.channel_names_secondary,
                    cn)

                results[cn] = {'data': channel_data_secondary[channel_index],
                               'time': time_array_secondary[channel_index]}

        return(results)

    # ------------------------------------------------------------------------------
    # Analyze metrics
    # -------------------------------------------------------------------------------

    def analyze_metric(self, metric_list, timewindow):
        """ Handling function for metrics

        Args:
            metric_list: <list> list of metrics requested (as strings)
             timewindow    : a two-element array specifying the time window.

        Returns:
            results: <list> list of the requested metrics
        """

        # take a snapshot of the data
        # -- primary data
        if self.primary_node:
            channel_data_copy, time_array_copy = self.data_snapshot(timewindow)
        else:
            channel_data_copy = None

        # -- secondary data
        if self.secondary_data:
            channel_data_copy_secondary, time_array_copy_secondary = self.data_snapshot_secondary(
                timewindow)
        else:
            channel_data_copy_secondary = None

        # dict that will contain the results
        results = {}

        for metric in metric_list:
            key = metric.replace(':', '_').replace(',', '_')
            tmp = metric.split(':')
            metric = tmp[0]

            # making sure we can find the channels
            if len(tmp) > 1:
                channels = tmp[1]
                if ',' in channels:
                    channels = channels.split(',')
                else:
                    channels = [channels]
                channels_found = set(
                    self.channel_names +
                    self.channel_names_secondary).issuperset(
                    set(channels))
            else:
                channels_found = False
            # -----------------------------------

            if metric in self.metric_names and channels_found:
                # the channel (or a list of channels)is always given as the
                # first argument and it is mandatory
                tmp = tmp[1:]

                # get the data for the selected channel(s)
                data = mu.get_channel_data(
                    channels,
                    channel_data_copy,
                    channel_data_copy_secondary,
                    self.channel_names,
                    self.channel_names_secondary)

                # analyze results using the parameters
                if len(tmp) > 1:
                    for i in range(len(tmp)):
                        try:
                            tmp[i] = float(tmp[i])
                        except ValueError:
                            pass
                    try:
                        results[key] = self.metric_pointers[
                            metric](data, *tmp[1:])
                    except TypeError as err:
                        results[key] = str(err)
                else:
                    try:
                        results[key] = self.metric_pointers[metric](data)
                    except TypeError as err:
                        results[key] = str(err)
            else:
                results[key] = 'unknown metric and/or channel'

        return results

    # --------------------------------------------------------------------------
    # Respond to queries ("orders for results") over ZeroMQ
    # --------------------------------------------------------------------------
    def analyze_command(self, command):
        """ Handling function for commands

            Args:
                command: a command (currently some very bugged format)
            Returns:
                return_value: return value of the command
        """
        command = "".join(command)
        return_value = []
        tmp = command.split(':')
        command = tmp[0]

        if len(tmp) > 1:
            params = tmp[1:]
        else:
            params = None

        if command == "get_metric_list":
            return_value = self.get_metric_list()
        elif command == "get_nodeinfo":
            return_value = self.get_nodeinfo()
        elif command == "get_publisher":
            return_value = self.get_publisher_url()
        elif command == "get_data_list":
            return_value = self.get_data_list()
        elif command == "get_topic_list":
            return_value = self.get_topic_list()
        else:
            return_value = "unknown command"

        return return_value

    # --------------------------------------------------------------------------
    # Start the node
    # --------------------------------------------------------------------------
    def start(self):
        """ Start the node. """
        self.run_state.value = 1

        # Add user-defined metrics to the metric list
        self.generate_metric_lists()

        # Create and configure beacon
        self.beacon = mu.Beacon(
            name=self.nodename,
            type=self.nodetype,
            id=self.nodeid,
            interval=2)
        self.beacon.ip = self.ip
        self.beacon.port = self.port_frontend

        # Start the load-balancing broker
        self.proc_broker = mp.Process(
            target=mu.LRU_queue_broker,
            args=(
                self.url_frontend,
                self.url_backend,
                self.n_workers,
                self.run_state))
        self.proc_broker.start()

        # Start the publisher if it is configured
        if self.run_publisher:
            self.proc_publisher = mp.Process(target=self.publisher)
            self.proc_publisher.start()

        # If the node is a primary node, start the receiver
        if self.primary_node:
            self.proc_receiver = mp.Process(target=self.receiver)
            self.proc_receiver.start()

        # Start responders
        self.proc_responder_list = [0] * self.n_workers

        for i in range(self.n_workers):
            self.proc_responder_list[i] = mp.Process(
                target=self.responder,
                args=(
                    i,
                    ))
            self.proc_responder_list[i].start()

        # Start user-defined processes, if there are any
        self.proc_user_list = [0] * len(self.process_list)

        for i, fn in enumerate(self.process_list):
            self.proc_user_list[i] = mp.Process(target=fn)
            self.proc_user_list[i].start()

        # Set the beacon online
        self.beacon.set_status('online')
        self.beacon.start()

        time.sleep(5)
        print("Node '%s' now online." % self.nodename)

    # --------------------------------------------------------------------------
    # Stop the node
    # --------------------------------------------------------------------------
    def stop(self):
        """ Terminates the node. """
        if self.run_state.value:

            print("Node '%s' shutting down ..." % self.nodename)

            self.beacon.set_status('offline')
            self.run_state.value = 0

            # Terminate responders
            for i in self.proc_responder_list:
                i.terminate()

            # Terminate user-defined processes, if there are any
            for i in self.proc_user_list:
                i.terminate()

            # Terminate broker
            self.proc_broker.join()

            # Stop receiver if it is running
            if self.primary_node:
                self.proc_receiver.join()

            # Stop the publisher if it is running
            if self.run_publisher:
                self.proc_publisher.join()

            # Stop the beacon
            self.beacon.stop()

        else:
            print("Node '%s' is not running." % self.nodename)

        print("Node '%s' is now offline." % self.nodename)

    # --------------------------------------------------------------------------
    # Minimalist user interface for the node
    # --------------------------------------------------------------------------
    def show_ui(self):
        """ Show a minimal user interface. """
        while True:
            tmp = input(" > ")
            if tmp == "q":
                self.stop()
                sys.exit(0)

    # --------------------------------------------------------------------------
    # Metrics
    # --------------------------------------------------------------------------
    def test(self):
        """ Toy metric function that returns 'heads' or 'tails'. """

        return random.choice(["ping", "pong"])

    # ------------------------------------------------------------------------------
    # Generate metric list
    # ------------------------------------------------------------------------------
    def generate_metric_lists(self):
        """ Generate metric lists for the node.

            metric_functions    : pointers to functions used to calculate
                                  metrics (array)
            metric_names        : the names of the metrics (array)
            metric_descriptions : dict with function names as key and
                                  description as value
            metric_pointers     : dict with function names as key and function
                                  pointer as value
        """
        self.metric_names = [m.__name__ for m in self.metric_functions]
        self.metric_descriptions = dict(
            zip(self.metric_names,
                [m.__doc__.split('\n')[0].strip()
                 for m in self.metric_functions]))
        self.metric_pointers = dict(
            zip(self.metric_names, self.metric_functions))

    def generate_nodeinfo(self):
        self.nodeinfo = {}
        self.nodeinfo['name'] = self.nodename
        self.nodeinfo['desc'] = self.nodedesc
        self.nodeinfo['primary_node'] = self.primary_node
        self.nodeinfo['channel_count'] = self.n_channels
        self.nodeinfo['channel_names'] = ",".join(self.channel_names)
        self.nodeinfo['channel_descriptions'] = ",".join(
            self.channel_descriptions)
        self.nodeinfo['sampling_rate'] = self.sampling_rate
        self.nodeinfo['buffer_size'] = self.buffer_size_s
        self.nodeinfo['buffer_full'] = self.buffer_full.value

    # --------------------------------------------------------------------------
    # Return descriptions of the metrics
    # --------------------------------------------------------------------------
    def get_metric_list(self):
        """ Returns the metrics list of the node as a dictionary where the name
            of the metric is the key and the description is the value.
        """

        return self.metric_descriptions

    # ---------------

    def get_topic_list(self):
        """ Return topics that are published by the node. """
        return self.topic_list

    # ---------------

    def get_nodeinfo(self):
        """ Return information about the node. """

        self.generate_nodeinfo()

        return self.nodeinfo

    # ---------------

    def get_publisher_url(self):
        """ Return the URL of the publisher socket in the node. """

        return self.url_publisher

    # ---------------

    def get_data_list(self):
        """ Returns the data list of the node as a dictionary where the name of the
            data is the key and the description is the value.
        """

        cn = self.channel_names + self.channel_names_secondary
        cd = self.channel_descriptions + self.channel_descriptions_secondary

        return dict(zip(cn, cd))
    # --------------------------------------------------------------------------
