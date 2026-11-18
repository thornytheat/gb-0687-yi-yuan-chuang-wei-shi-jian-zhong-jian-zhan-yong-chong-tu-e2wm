from flask import render_template,request
from app import store
from app.pipeline import run
from app.config import TITLE,MODULES,STAGES,COLUMNS,SAMPLES,DEFAULTS

def _load(stage, run_id):
    if not run_id:
        return run(stop=8 if stage == 8 else 7)
    result = store.fetch(run_id)
    if stage == 8 and len(result.get('stages', [])) == 7:
        # 已封存批次只含 M1-M7；用封存的原始输入与相同参数即时补算身份接回，不改动原批次
        result = run(result.get('source_text'), result['manifest']['params'], stop=8, persist=False)
    return result

def view(stage):
    if not 1<=stage<=8: return "页面不存在",404
    run_id=request.args.get("run")
    result=_load(stage, run_id)
    sample=(SAMPLES/"dirty.csv").read_text(encoding="utf-8-sig")
    data=result["stages"][stage-1]
    from datetime import datetime
    items=result.get("identity_intervals") if stage==8 else result["intervals"]
    start=min((datetime.fromisoformat(x["start"]).timestamp() for x in items),default=0)
    end=max((datetime.fromisoformat(x["end"]).timestamp() for x in items),default=1)
    tracks=[]
    for bed in sorted({x["bed"] for x in items}):
        bars=[]
        for x in items:
            if x["bed"]==bed:
                bars.append({**x,"left":(datetime.fromisoformat(x["start"]).timestamp()-start)/(end-start or 1)*100,"width":(datetime.fromisoformat(x["end"]).timestamp()-datetime.fromisoformat(x["start"]).timestamp())/(end-start or 1)*100})
        tracks.append({"bed":bed,"bars":bars})
    return render_template(f"m{stage}.html",title=TITLE,modules=MODULES,stage=stage,result=result,data=data,
                           sample=sample,defaults=DEFAULTS,columns=COLUMNS,history=store.history(),tracks=tracks)
