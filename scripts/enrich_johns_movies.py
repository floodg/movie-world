#!/usr/bin/env python3
"""Enrich John's SQLite catalogue using TMDB API v3. Python stdlib only."""
import argparse, datetime, json, os, sqlite3, time, urllib.parse, urllib.request, urllib.error

def api(path, key, params=None):
    p=dict(params or {}); p['api_key']=key
    url='https://api.themoviedb.org/3/'+path.lstrip('/')+'?'+urllib.parse.urlencode(p)
    for attempt in range(6):
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'JohnsMovieCatalogue/1.0'}),timeout=25) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (401,403): raise RuntimeError('TMDB credentials rejected') from e
            if e.code==404: return {}
            if e.code not in (429,500,502,503,504): raise
            delay=int(e.headers.get('Retry-After','0') or 0) or min(30,2**attempt)
        except (urllib.error.URLError, TimeoutError): delay=min(30,2**attempt)
        time.sleep(delay)
    raise RuntimeError('TMDB request failed after retries: '+path)

def choose(results, title, year):
    import difflib
    scored=[]
    for x in results:
        name=x.get('title') or x.get('name') or ''
        y=(x.get('release_date') or x.get('first_air_date') or '')[:4]
        sim=difflib.SequenceMatcher(None,title.casefold(),name.casefold()).ratio()
        if year and y and abs(int(y)-year)>1: continue
        if sim>=.94 and (not year or (y and abs(int(y)-year)<=1)):
            scored.append((sim,x))
    scored.sort(key=lambda v:v[0],reverse=True)
    if not scored or (len(scored)>1 and scored[0][0]-scored[1][0]<.025): return None
    return scored[0][1]

def main():
    p=argparse.ArgumentParser();p.add_argument('database');p.add_argument('--key',default=os.getenv('TMDB_API_KEY'));p.add_argument('--limit',type=int,default=0);p.add_argument('--delay',type=float,default=.28);a=p.parse_args()
    if not a.key: p.error('Set TMDB_API_KEY or pass --key')
    con=sqlite3.connect(a.database)
    cols={r[1] for r in con.execute('pragma table_info(movies)')}
    for name,typ in [('tmdb_id','INTEGER'),('poster_url','TEXT'),('backdrop_url','TEXT'),('overview','TEXT'),('runtime_minutes','INTEGER'),('tmdb_status','TEXT'),('tmdb_updated_at','TEXT')]:
        if name not in cols: con.execute(f'ALTER TABLE movies ADD COLUMN {name} {typ}')
    con.commit()
    rows=con.execute("SELECT id,title,release_year,imdb_id,media_type FROM movies WHERE tmdb_status IS NULL OR tmdb_status IN ('error','pending') ORDER BY CASE WHEN imdb_id IS NULL THEN 1 ELSE 0 END,id").fetchall()
    ok=miss=errors=0
    for index,(id,title,year,imdb_id,media_type) in enumerate(rows[:a.limit or None],1):
        try:
            item=None; kind='movie'
            if imdb_id:
                found=api('find/'+urllib.parse.quote(imdb_id),a.key,{'external_source':'imdb_id'})
                for typ,field in [('movie','movie_results'),('tv','tv_results')]:
                    hits=found.get(field) or []
                    if len(hits)==1: item=hits[0];kind=typ;break
            if not item and title:
                for typ in (['tv'] if media_type in ('tvSeries','tvMiniSeries') else ['movie','tv']):
                    q={'query':title}
                    if year: q['year' if typ=='movie' else 'first_air_date_year']=year
                    result=api('search/'+typ,a.key,q)
                    item=choose(result.get('results',[]),title,year)
                    if item:kind=typ;break
            if item:
                detail=api(kind+'/'+str(item['id']),a.key)
                poster=detail.get('poster_path');back=detail.get('backdrop_path')
                con.execute('''UPDATE movies SET tmdb_id=?,poster_url=?,backdrop_url=?,overview=?,runtime_minutes=?,tmdb_status='matched',tmdb_updated_at=? WHERE id=?''',(
                    detail['id'],'https://image.tmdb.org/t/p/w500'+poster if poster else None,
                    'https://image.tmdb.org/t/p/w1280'+back if back else None,
                    detail.get('overview'),detail.get('runtime') or (detail.get('episode_run_time') or [None])[0],datetime.datetime.now(datetime.timezone.utc).isoformat(),id))
                ok+=1
            else:
                con.execute("UPDATE movies SET tmdb_status='not_found',tmdb_updated_at=? WHERE id=?",(datetime.datetime.now(datetime.timezone.utc).isoformat(),id));miss+=1
        except Exception as e:
            con.execute("UPDATE movies SET tmdb_status='error',tmdb_updated_at=? WHERE id=?",(datetime.datetime.now(datetime.timezone.utc).isoformat(),id));errors+=1
            print(f'ERROR {id}: {e}',flush=True)
        con.commit()
        if index%100==0: print(f'{index}/{len(rows)} processed: matched={ok} not_found={miss} errors={errors}',flush=True)
        time.sleep(a.delay)
    print(f'Finished: matched={ok}, not_found={miss}, errors={errors}. Database: {a.database}')
if __name__=='__main__': main()
