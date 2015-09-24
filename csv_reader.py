import tailer 
import parse 
import argparse
import sys
import json
import logging
from datetime import datetime

import csv

# Test function for sensor full name parsing

class SensorDataDictItem(object):
    def __init__(self, ndnNameString, dataType):
        self._ndnNameString = ndnNameString
        self._dataType = dataType

class UniqueNameTailer(object):
    def __init__(self):
        self._nameDict = []
        #self._maxCount = 200
        self._maxCount = 9999999999

        self._ndnNameDict = dict()

    def parseLine(self, line):
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
            
        self.pointNameToNDNName(point[0])
        
    def readfile(self, filename):
        f = open(filename, 'r')
        count = 0
        for line in f:
            if count > self._maxCount:
                break
            self.parseLine(line)
            count += 1
        f.close()

    def pointNameToNDNName(self, name):
        name = name.lower().split(":")[1]
        
        # For test code
        if not name in self._nameDict:
            #print(name)
            self._nameDict.append(name)

        return name

def main():
    nameTailer = UniqueNameTailer()
    nameTailer.readfile('ucla-datahub-Feb2.log')


    with open('bms-sensor-data-types.csv', 'rU') as csvfile:
        reader = csv.reader(csvfile, delimiter=',', quotechar='|')

        # limit = 10
        limit = 9999999999
        current = 0

        for row in reader:
            # Some rows in the csv are wrongfully parsed
            if current > limit:
                break

            if (len(row)) > 5:
                # sensor full name, building name, room name, sensor name, sensor data type
                #print(row[1], row[2], row[3], row[4], row[5])
                key = ''
                if (row[3] != ''):
                    key = row[2].lower().strip() + '.' + row[3].lower().strip() + '.' + row[4].lower().strip()
                    ndnNameString = row[2].lower().strip() + '/' + row[3].lower().strip() + '/' + row[4].lower().strip()
                    nameTailer._ndnNameDict[key] = SensorDataDictItem(ndnNameString, row[5])
                else:
                    key = row[2].lower().strip() + '.' + row[4].lower().strip()
                    ndnNameString = row[2].lower().strip()+ '/' + row[4].lower().strip()
                    nameTailer._ndnNameDict[key] = SensorDataDictItem(ndnNameString, row[5])

                if (row[5] == ''):
                    print(key + ' does not have a data type')

            current += 1

    print("\n")
    foundCnt = 0
    notFoundCnt = 0

    for item in nameTailer._nameDict:
        #print(item)
        if (item in nameTailer._ndnNameDict):
            #print('Found ' + item)
            foundCnt += 1
        else:
            print('Not found ' + item)
            notFoundCnt += 1
    
    print('Found Count: ' + str(foundCnt))
    print('Not found Count: ' + str(notFoundCnt))

    # Data type decision:
    # Component 1 - row[2]

'''
Not found neurosci_rch.121.xfmr-a.dmd : This has an extra 'space' in the source xlsx, workaround with strip
Not found botany.chw-tsins            : We don't have this indeed in the xlsx, instead we have botany.chw-tsint, oversight?
Not found neurosci_rch.chw-fins       : We don't have this indeed in the xlsx, instead we have neurosci_rch.chw-fins, oversight?
'''

if __name__ == '__main__':
    main()