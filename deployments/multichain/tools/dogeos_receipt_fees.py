"""Actual DogeOS receipt fees, including the data/finality component.

https://docs.dogeos.com/en/developers/transaction-fees-on-dogeos
The RPC receipt exposes the data/finality fee as l1Fee. Missing fee data is
unknown, never a zero-fee assumption. This helper does not estimate fees.
"""
import re


def receipt_fees(receipt):
    def number(name):
        value = receipt.get(name)
        if isinstance(value, bool):
            raise ValueError('Invalid receipt fee field: '+name)
        if isinstance(value, int) and value >= 0:
            return value
        if isinstance(value, str) and re.fullmatch(r'(?:0x[0-9a-fA-F]+|[0-9]+)', value):
            return int(value, 16 if value.startswith('0x') else 10)
        raise ValueError('Missing or invalid receipt fee field: '+name)

    execution = number('gasUsed') * number('effectiveGasPrice')
    data_finality = number('l1Fee')
    return dict(executionFeeWei=str(execution), dataFinalityFeeWei=str(data_finality),
                totalFeeWei=str(execution+data_finality))
