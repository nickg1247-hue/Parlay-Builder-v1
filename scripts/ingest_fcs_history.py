import argparse,json
from app.ingest.fcs_history import ingest
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--refresh",action="store_true");a=p.parse_args();_,m=ingest(refresh=a.refresh);print(json.dumps(m,indent=2))
