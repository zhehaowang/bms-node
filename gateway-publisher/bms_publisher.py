import tailer 
import parse 
import argparse
import sys
import json
import logging
from datetime import datetime

import subprocess
import time
import select

import csv

try:
    import asyncio
except ImportError:
    import trollius as asyncio
    from concurrent.futures import ProcessPoolExecutor

from pyndn import Name, Data
from pyndn.threadsafe_face import ThreadsafeFace
from pyndn.security import KeyChain
#from pyndn.security.identity import FilePrivateKeyStorage, BasicIdentityStorage
#from pyndn.security.identity import IdentityManager
from pyndn.util.memory_content_cache import MemoryContentCache

T = datetime.strptime("2015-02-05", "%Y-%m-%d")

# Value of dictionary _sensorNDNDict
#       key:    string, string name of sensor from given csv and received sensor data JSON
#       value:  _aggregationName, Name, the ndn-ized aggregation data name prefix
#               _instName, Name, the ndn-ized instantaneous data name
class SensorNDNDictItem(object):
    def __init__(self, aggregationName, instName):
        self._aggregationName = aggregationName
        self._instName = instName

class DataQueueItem(object):
    def __init__(self, dataList, timeThreshold):
        self._dataList = dataList
        self._timeThreshold = timeThreshold

class DataPublisher(object):
    def __init__(self, face, keyChain, loop, cache, namespace):
        # Start time of this instance
        self._startTime = 0
        # Default aggregation interval in seconds
        self._defaultInterval = 10000
        # Dictionary that holds the temporary data to calculate aggregation with
        # Key   - sensor name
        # Value - data list: [], list of sensor data
        #         timeThreshold: int, any data before this timestamp should be used for aggregation calculation; 
        #                             here we assume for each sensor, its data would come in order on this node
        self._dataQueue = dict()
        self._face = face
        self._keyChain = keyChain
        self._loop = loop
        self._cache = cache
        self._namespace = namespace
        self.DEFAULT_DATA_LIFETIME = 2000000

        self._sensorNDNDict = dict()

    def populateSensorNDNDictFromCSV(self, csvFileName):
        with open(csvFileName, 'rU') as csvFile:
            reader = csv.reader(csvFile, delimiter=',', quotechar='|')

            for row in reader:
                if (len(row)) > 5:
                    # sensor full name, building name, room name, sensor name, sensor data type
                    #print(row[1], row[2], row[3], row[4], row[5])
                    key = ''
                    if (row[3] != ''):
                        key = row[2].lower().strip() + '.' + row[3].lower().strip() + '.' + row[4].lower().strip()
                        ndnNameString = row[2].lower().strip() + '/' + row[3].lower().strip() + '/' + row[4].lower().strip()
                        aggregationName = Name(ndnNameString).append('data').append(row[5]).append('aggregation')
                        instName = Name(ndnNameString).append('data').append(row[5]).append('inst')

                        self._sensorNDNDict[key] = SensorNDNDictItem(aggregationName, instName)
                    else:
                        key = row[2].lower().strip() + '.' + row[4].lower().strip()
                        ndnNameString = row[2].lower().strip() + '/' + row[4].lower().strip()
                        aggregationName = Name(ndnNameString).append('data').append(row[5]).append('aggregation')
                        instName = Name(ndnNameString).append('data').append(row[5]).append('inst')

                        self._sensorNDNDict[key] = SensorNDNDictItem(aggregationName, instName)

    def publish(self, line):
        # Pull out and parse datetime for log entry 
        # (note we shoudld use point time for timestamp)
        try:
            if not ": (point" in line: return
            dateTimeStr = parse.search("[{}]", line)[0]
            point = parse.search("(point {})", line)[0].split(" ")
        except Exception as detail:
            print("publish: Parse error for", line, "-", detail)
            return
        try:
            dateTime = datetime.strptime(dateTimeStr, "%Y-%m-%d %H:%M:%S.%f")
        except Exception as detail:
            print("publish: Date/time conversion error for", line, "-", detail)
            return
            
        sensorName = point[0]
        namePrefix = self.pointNameToNDNName(sensorName)
        dataDict = self.pointToJSON(point)
        
        if namePrefix is not None:
            #if dateTime < T: return
            #if __debug__:
            #    print(dateTime, namePrefix, dataDict["timestamp"], "payload:", dataDict["value"])
            try:
                # Timestamp in data name uses the timestamp from data paylaod
                dataTemp = self.createData(namePrefix, dataDict["timestamp"], dataDict["value"])
                if __debug__:
                    print("Produced raw data name " + dataTemp.getName().toUri())
                    print("Produced raw data content " + dataTemp.getContent().toRawStr())
                self._cache.add(dataTemp)

                # TODO: since the leaf sensor publisher is not a separate node for now, we also publish aggregated data
                #       of the same sensor over the past given time period in this code;
                #       bms_node code has adaptation for leaf sensor publishers as well, ref: example-sensor1.conf

                # Here we make the assumption of fixed time window for *all* sensors
                dataTime = int(float(dataDict["timestamp"]) * 1000)
                if self._startTime == 0:
                    self._startTime = dataTime
                if not (sensorName in self._dataQueue):
                    self._dataQueue[sensorName] = DataQueueItem([], self._startTime + self._defaultInterval)
                    self._dataQueue[sensorName]._dataList.append(dataDict["value"])
                elif dataTime > self._dataQueue[sensorName]._timeThreshold:
                    # calculate the aggregation with what's already in the queue, publish data packet, and delete current queue
                    # TODO: This should be mutex locked against self
                    if len(self._dataQueue[sensorName]._dataList) > 0:
                        avg = 0.0
                        for item in self._dataQueue[sensorName]._dataList:
                            avg += float(item)
                        avg = avg / len(self._dataQueue[sensorName]._dataList)
                        data = Data(Name(namePrefix).append(str(self._dataQueue[sensorName]._timeThreshold)).append(str(self._dataQueue[sensorName]._timeThreshold + self._defaultInterval)))
                        data.setContent(str(avg))
                        data.getMetaInfo().setFreshnessPeriod(self.DEFAULT_DATA_LIFETIME)
                        self._cache.add(data)
                        print("Aggregation produced " + data.getName().toUri())

                    self._dataQueue[sensorName]._dataList = [dataDict["value"]]
                    self._dataQueue[sensorName]._timeThreshold = self._dataQueue[sensorName]._timeThreshold + self._defaultInterval
                else:
                    self._dataQueue[sensorName]._dataList.append(dataDict["value"])
                
            except Exception as detail:
                print("publish: Error calling createData for", line, "-", detail)

    def createData(self, name, timestamp, payload):
        data = Data(Name(name + "/" + str(timestamp))) 
        data.setContent(payload)
        #keyChain.sign(data, keyChain.getDefaultCertificateName())
        data.getMetaInfo().setFreshnessPeriod(self.DEFAULT_DATA_LIFETIME)
        if __debug__:
            print("ndn:" + name + "/" + str(timestamp) + "\t:" + payload)
        return data
    
    # @param {Boolean} isAggregation, True for return sensorName/data/aggregation/type, False for return sensorName/data/raw/type
    # @return {NDN name | None} None if asked for aggregation and string name entry is not found; NDN name is string entry is found, or can be inferred
    def pointNameToNDNName(self, point, isAggregation = True):
        name = point.lower().split(":")[1]
        
        # We expect failures in name transformation to be as little as possible; for our current historical data, 3 out of ~800 sensor names cannot be found
        if (name in self._sensorNDNDict):
            if isAggregation:
                return self._sensorNDNDict[name]._aggregationName
            else:
                return self._sensorNDNDict[name]._instName
        else:
            print("Sensor name " + name + " not found in dict, aggregation calculation skipped; raw publishing with assumed name")
            if isAggregation:
                return None
            else:
                return Name(name.replace('.', '/'))


    def pointToJSON(self, pd):
        d = {}
        args = ["pointname", "type", "value", "conf", "security", "locked", "seconds", "nanoseconds", "unknown_1", "unknown_2"]
        for i in range(len(args)):
            try:
                d[args[i]] = pd[i]
            except Exception as detail:
                d[args[i]] = None
                print("pointToJSON: Error parsing arg", args[i], "from", pd, "-", detail)
        try:
            timestamp = (int(d["seconds"]) + int(d["nanoseconds"])*1e-9)
            dt = datetime.fromtimestamp(timestamp)
            d["timestamp_str"] = dt.strftime("%Y-%m-%d %H:%M:%S.%f")
            d["timestamp"] = str(timestamp)
        except Exception as detail:
            print("pointToJSON: Error in timestamp conversation of", pd)
            d["timestamp"] = 0
            d["timestamp_str"] = ("0000-00-00 00:00:00.00")
        try:
            if __debug__:
                print(json.dumps(d))
        except Exception as detail:
            print("pointToJSON: Error in JSON conversation of", pd)
            return "{}"
        return d

    @asyncio.coroutine
    def readfile(self, filename):
        f = open(filename, 'r')
        for line in f:
            self.publish(line)
        f.close()
        
    @asyncio.coroutine
    def followfile(self, filename):
        f = subprocess.Popen(['tail','-F', filename],\
              stdout = subprocess.PIPE,stderr = subprocess.PIPE)
        p = select.poll()
        p.register(f.stdout)

        while True:
            if p.poll(1):
                self.publish(f.stdout.readline())
            time.sleep(0.01)
            yield None

        #for line in tailer.follow(open(filename)):
        #    publish(line, namespace, cache)

    def onRegisterFailed(self, prefix):
        print("register failed for " + prefix.getName().toUri())
        raise RuntimeError("Register failed for prefix", prefix.toUri())

    def onDataNotFound(self, prefix, interest, face, interestFilterId, filter):
        print('DataNotFound')
        return

class Logger(object):
    def prepareLogging(self):
        self.log = logging.getLogger(str(self.__class__))
        self.log.setLevel(logging.DEBUG)
        logFormat = "%(asctime)-15s %(name)-20s %(funcName)-20s (%(levelname)-8s):\n\t%(message)s"
        self._console = logging.StreamHandler()
        self._console.setFormatter(logging.Formatter(logFormat))
        self._console.setLevel(logging.INFO)
        # without this, a lot of ThreadsafeFace errors get swallowed up
        logging.getLogger("trollius").addHandler(self._console)
        self.log.addHandler(self._console)

    def setLogLevel(self, level):
        """
        Set the log level that will be output to standard error
        :param level: A log level constant defined in the logging module (e.g. logging.INFO) 
        """
        self._console.setLevel(level)

    def getLogger(self):
        """
        :return: The logger associated with this node
        :rtype: logging.Logger
        """
        return self.log

def main(): 
    # Params parsing
    parser = argparse.ArgumentParser(description='bms gateway node to Parse or follow Cascade Datahub log and publish to MiniNdn.')
    parser.add_argument('filename', help='datahub log file')
    parser.add_argument('-f', dest='follow', action='store_true', help='follow (tail -f) the log file')  
    parser.add_argument('--namespace', default='/ndn/edu/ucla/remap/bms', help='root of ndn name, no trailing slash')
    args = parser.parse_args()
    
    # Setup logging
    logger = Logger()
    logger.prepareLogging()

    # Face, KeyChain, memoryContentCache and asio event loop initialization
    loop = asyncio.get_event_loop()
    face = ThreadsafeFace(loop)

    #keychain = KeyChain(IdentityManager(BasicIdentityStorage(), FilePrivateKeyStorage()))
    keyChain = KeyChain()
    face.setCommandSigningInfo(keyChain, keyChain.getDefaultCertificateName())
    cache = MemoryContentCache(face)

    dataPublisher = DataPublisher(face, keyChain, loop, cache, args.namespace)
    cache.registerPrefix(Name(args.namespace), dataPublisher.onRegisterFailed, dataPublisher.onDataNotFound)
    
    # Parse csv to decide the mapping between sensor JSON -> <NDN name, data type>
    dataPublisher.populateSensorNDNDictFromCSV('../bms-sensor-data-types.csv')

    if args.follow: 
        #asyncio.async(loop.run_in_executor(executor, followfile, args.filename, args.namespace, cache))
        loop.run_until_complete(dataPublisher.followfile(args.filename))
    else:
        loop.run_until_complete(dataPublisher.readfile(args.filename))

    loop.run_forever()
    face.shutdown()
        
if __name__ == '__main__':
    main()