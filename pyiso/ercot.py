from bs4 import BeautifulSoup
from pyiso.base import BaseClient
import pandas as pd
from io import StringIO
import re
from datetime import datetime
import pytz


class ERCOTClient(BaseClient):
    NAME = 'ERCOT'
    base_report_url = 'http://mis.ercot.com'
    real_time_url = 'http://www.ercot.com/content/cdr/html/real_time_system_conditions.html'

    report_type_ids = {
        'wind_5min': '13071',
        'wind_hrly': '13028',
        'gen_hrly': '12358',
        'load_7day': '12311'
    }

    TZ_NAME = 'US/Central'

    def _request_report(self, report_type, date=None):
        # request reports list
        params = {'reportTypeId': self.report_type_ids[report_type]}
        response = self.request(self.base_report_url+'/misapp/GetReports.do',
                                params=params)
        if not response:
            raise ValueError('ERCOT: No report available for %s' % report_type)
        report_list_soup = BeautifulSoup(response.content, 'lxml')

        # Round minute down to nearest 5 minute period
        if date:
            date = datetime(date.year, date.month, date.day, date.hour,
                            date.minute - (date.minute % 5), tzinfo=date.tzinfo)
            date = pytz.timezone(self.TZ_NAME).normalize(date)

        # find the endpoint to download
        report_endpoint = None
        for elt in report_list_soup.find_all('tr'):
            label = elt.find(class_='labelOptional_ind')
            if label and 'csv' in label.string:
                if date and label.string.split('.')[3] != date.strftime('%Y%m%d'):
                    continue
                report_endpoint = self.base_report_url + elt.a.attrs['href']
                break

        # test endpoint found
        if not report_endpoint:
            raise ValueError(
                'ERCOT: No report available for %s' % report_type)

        # read report from zip
        r = self.request(report_endpoint)
        if r:
            content = self.unzip(r.content)
        else:
            return pd.DataFrame()

        # parse csv
        df = pd.read_csv(StringIO(content[0].decode('unicode_escape')))
        df.columns = [x.strip() for x in df.columns]
        df = df.dropna(axis=0)

        # return
        return df

    def is_dst(self, val, standard):
        return val != standard

    def get_generation(self, latest=False, **kwargs):
        # set args
        self.handle_options(data='gen', latest=latest, **kwargs)

        if self.options['latest']:
            # get latest load site
            response = self.request(self.real_time_url)

            # parse load from response
            if response:
                data = self.parse_rtm(response.text)
            else:
                data = []

        else:
            raise ValueError('Only latest genmix data available in ERCOT')

        # return
        return data

    def get_load(self, latest=False, **kwargs):
        # set args
        self.handle_options(data='load', latest=latest, **kwargs)

        if self.options['latest']:
            # get latest load site
            response = self.request(self.real_time_url)

            # parse load from response
            if response:
                data = self.parse_rtm(response.text)
            else:
                data = []

        elif self.options['forecast']:
            # get 7 day forecast load
            try:
                df = self._request_report('load_7day')
            except ValueError:
                return []

            # convert column of hour ending (1:00-24:00) to hour beginning (0:00-23:00)
            df['HourBeginning'] = df.apply(lambda dp: int(dp['HourEnding'].split(':')[0])-1,
                                           axis=1)

            # create datetime index of hour beginning
            df.index = df.apply(lambda dp: self.utcify(pd.to_datetime('%s %d:00' % (dp['DeliveryDate'], dp['HourBeginning'])),
                                                       is_dst=self.is_dst(dp['DSTFlag'], 'N')),
                                axis=1)

            # slice times
            sliced = self.slice_times(df)

            # pull out total load series
            series = sliced['SystemTotal']
            series.name = 'load_MW'
            series.index.set_names(['timestamp'], inplace=True)

            # slice and format
            extras = {
                'ba_name': self.NAME,
                'market': self.MARKET_CHOICES.dam,
                'freq': self.FREQUENCY_CHOICES.hourly,
            }
            data = self.serialize_faster(series, extras=extras)

        else:
            raise ValueError('Load only available for latest or forecast in ERCOT')

        # return
        return data

    def parse_rtm(self, content):
        # make soup
        soup = BeautifulSoup(content, 'lxml')

        # timestamp text starts with 'Last Updated'
        timestamp_elt = soup.find(text=re.compile('Last Updated'))
        timestamp_str = timestamp_elt.strip('Last Updated: ')
        timestamp = self.utcify(timestamp_str)

        #print("Timestamp:" + timestamp_str)

        # parse rest of page as html table
        df = pd.read_html(content, index_col=0)[0]
        #print("Pandas Data Frame:"+df.head())

        # get other values
        load_val = float(df.loc['Actual System Demand'][1])
        #print("load_val:"+str(load_val))
        wind_val = float(df.loc['Total Wind Output'][1])
        #print("wind_val:"+str(wind_val))
        tie_flow_labels = ['DC_E (East)', 'DC_L (Laredo VFT)', 'DC_N (North)',
                           'DC_R (Railroad)', 'DC_S (Eagle Pass)']
        total_imports_val = sum([float(df.loc[label][1]) for label in tie_flow_labels])
        #print("total_imports_val:"+str(total_imports_val))

        # use options to get labels
        if self.options['data'] == 'load':
            data = [
                {
                    'timestamp': timestamp,
                    'ba_name': self.NAME,
                    'market': self.options.get('market', self.MARKET_CHOICES.fivemin),
                    'freq': self.options.get('freq', self.FREQUENCY_CHOICES.fivemin),
                    'load_MW': load_val,
                },
            ]
        elif self.options['data'] == 'gen':
            data = [
                {
                    'timestamp': timestamp,
                    'ba_name': self.NAME,
                    'market': self.options.get('market', self.MARKET_CHOICES.fivemin),
                    'freq': self.options.get('freq', self.FREQUENCY_CHOICES.fivemin),
                    'fuel_name': 'wind',
                    'gen_MW': wind_val,
                },
                {
                    'timestamp': timestamp,
                    'ba_name': self.NAME,
                    'market': self.options.get('market', self.MARKET_CHOICES.fivemin),
                    'freq': self.options.get('freq', self.FREQUENCY_CHOICES.fivemin),
                    'fuel_name': 'nonwind',
                    'gen_MW': (load_val - total_imports_val) - wind_val,
                },
            ]
        else:
            raise ValueError('Cannot get real-time data for %s' % self.options['data'])

        # return
        return data

    def handle_options(self, **kwargs):
        super(ERCOTClient, self).handle_options(**kwargs)

        # ensure market and freq are set
        if 'market' not in self.options:
            if self.options['forecast']:
                self.options['market'] = self.MARKET_CHOICES.dam
            else:
                self.options['market'] = self.MARKET_CHOICES.dam
        if 'freq' not in self.options:
            if self.options['forecast']:
                self.options['freq'] = self.FREQUENCY_CHOICES.hourly
            else:
                self.options['freq'] = self.FREQUENCY_CHOICES.fivemin

        if 'latest' not in self.options:
            self.options['latest'] = False
        if 'forecast' not in self.options:
            self.options['forecast'] = False
