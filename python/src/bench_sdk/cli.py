"""Noninteractive bench eval commands; API decisions are the source of truth."""
import argparse
import csv
import io
import json
import os
from pathlib import Path
import sys
import time

from .evaluations import EvaluationClient


def gate(run):
    result=run['result']
    if run['status'] in ('failed','canceled'): return 1
    if run['status']!='completed' or result.get('decision') in (None,'insufficient_evidence','observed_improvement'): return 3
    return 0 if result['decision']=='validated_improvement' else 2


def main():
    parser=argparse.ArgumentParser(prog='bench')
    parser.add_argument('area',choices=['eval'])
    parser.add_argument('command',choices=['plan','run','status','watch','cancel','resume','results','compare','export','gate','review'])
    parser.add_argument('id',nargs='?')
    parser.add_argument('--system',type=int,required=True)
    parser.add_argument('--api',default=os.getenv('BENCH_API_URL','https://api.usebench.ai'))
    parser.add_argument('--file');parser.add_argument('--key');parser.add_argument('--after',type=int,default=0)
    parser.add_argument('--json',action='store_true',help='JSON is the default; watch emits JSON lines')
    parser.add_argument('--format',choices=['json','csv','markdown'],default='json')
    parser.add_argument('--output');parser.add_argument('--case');parser.add_argument('--verdict',choices=['good','bad','skip']);parser.add_argument('--note',default='')
    args=parser.parse_args()
    try:
        token=os.environ.get('BENCH_API_KEY')
        if not token: raise ValueError('Set BENCH_API_KEY to an authorized automation credential')
        client=EvaluationClient(args.api,token,args.system)
        if args.command=='plan':
            if not args.file: raise ValueError('plan requires --file')
            result=client.plan(json.loads(Path(args.file).read_text()))
        else:
            if not args.id: raise ValueError('Provide a plan or run ID')
            if args.command in ('run','resume'):
                if not args.key: raise ValueError('Supply --key for idempotent execution')
                result=client.run(args.id,args.key) if args.command=='run' else client.resume(args.id,args.key)
            elif args.command=='cancel': result=client.cancel(args.id)
            elif args.command=='review':
                result=client.review(args.id,args.case,args.verdict,args.note) if args.case and args.verdict else client.review_cases(args.id)
            elif args.command=='watch':
                cursor=args.after
                while True:
                    result=client.events(args.id,cursor)
                    for event in result['events']:
                        print(json.dumps(event),flush=True);cursor=max(cursor,event['cursor'])
                    if result['status'] not in ('queued','running'):
                        print(json.dumps({'status':result['status'],'next_cursor':cursor}));return 0
                    time.sleep(2)
            else: result=client.status(args.id)
        output=json.dumps(result,indent=2)
        if args.command=='compare': output=json.dumps(result['result'].get('comparisons',[]),indent=2)
        if args.command=='export' and args.format=='csv':
            buffer=io.StringIO();writer=csv.writer(buffer);writer.writerow(['configuration','metric','value'])
            for config,metrics in result['result'].get('metrics',{}).items():
                for metric,value in metrics.items():
                    if not isinstance(value,(dict,list)): writer.writerow([config,metric,value])
            output=buffer.getvalue()
        if args.command=='export' and args.format=='markdown':
            data=result['result'];output=f"# Bench runtime evaluation {result['id']}\n\nStatus: {result['status']}\n\nDecision: {data.get('decision')}\n\n```json\n{json.dumps(data.get('comparisons',[]),indent=2)}\n```\n\nMeasurements describe the configured test environment.\n"
        if args.output: Path(args.output).write_text(output+'\n')
        else: print(output)
        return gate(result) if args.command=='gate' else 0
    except KeyboardInterrupt: return 1
    except Exception as error:
        print(json.dumps({'error':str(error)}),file=sys.stderr);return 1


if __name__=='__main__': sys.exit(main())
