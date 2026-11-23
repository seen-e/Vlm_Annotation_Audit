from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any


HTML = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RoboDojo 图文审核</title><style>
:root{font-family:Inter,"Microsoft YaHei",sans-serif;color:#17202a;background:#eef1f4}*{box-sizing:border-box}
body{margin:0;height:100vh;overflow:hidden}.app{display:grid;grid-template-columns:300px 1fr;height:100vh}
aside{background:#fff;border-right:1px solid #d9dee5;display:flex;flex-direction:column;min-height:0}.side-head{padding:18px;border-bottom:1px solid #e3e7eb}.side-head h1{font-size:18px;margin:0 0 10px}.stats{font-size:12px;color:#59636e}.filters{display:flex;gap:6px;flex-wrap:wrap;margin-top:12px}.filters button,.tabs button{border:1px solid #ccd3da;background:#fff;padding:7px 10px;cursor:pointer}.filters button.active,.tabs button.active{background:#17202a;color:#fff;border-color:#17202a}.list{overflow:auto;padding:8px}.ep{width:100%;text-align:left;border:1px solid #e0e4e8;background:#fff;margin-bottom:7px;padding:10px;cursor:pointer}.ep.active{border-color:#17202a;background:#f5f7f8}.ep-id{font-weight:700}.badge{display:inline-block;margin-top:6px;padding:3px 7px;font-size:11px;font-weight:700}.normal,.screened_pass{background:#dff3e8;color:#17643b}.annotation_anomaly,.inconsistent{background:#fde2df;color:#9c2c21}.task_not_met,.not_met{background:#fff0c9;color:#805600}.uncertain{background:#e7e4fb;color:#4e4394}.consistent,.met{background:#dff3e8;color:#17643b}
main{overflow:auto;padding:20px 24px}.header{max-width:1400px;margin:auto}.header h2{margin:0 0 8px}.summary{background:#fff;border:1px solid #dce1e6;padding:16px;margin:14px 0}.summary p{margin:8px 0;line-height:1.55}.status-row{display:flex;gap:8px;flex-wrap:wrap}.status-row span{padding:5px 8px;font-size:12px;font-weight:700}.issues{border-left:4px solid #c23b31;background:#fff;padding:12px 16px;margin:12px 0}.issues h3{margin:0 0 8px;font-size:15px}.issue{border-top:1px solid #e5e8eb;padding:10px 0}.tabs{display:flex;gap:6px;overflow:auto;margin:16px 0;padding-bottom:4px}.panel{background:#fff;border:1px solid #dce1e6;padding:16px;margin-bottom:18px}.grid{display:grid;grid-template-columns:minmax(300px,.8fr) minmax(420px,1.2fr);gap:16px}.block{border:1px solid #e0e4e8;padding:12px}.block h3{font-size:14px;margin:0 0 10px}.block p{white-space:pre-wrap;line-height:1.55;margin:7px 0}.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:10px}.frame{border:1px solid #dce1e6;background:#fafafa}.frame img{width:100%;display:block;aspect-ratio:4/3;object-fit:contain;background:#111}.cap{padding:8px;font-size:12px;line-height:1.45}.cap strong{display:block;margin-bottom:3px}.muted{color:#66717c}.evidence{border-left:3px solid #3f6f92;padding-left:9px;margin:8px 0}@media(max-width:900px){.app{grid-template-columns:230px 1fr}.grid{grid-template-columns:1fr}}
</style></head><body><div class="app"><aside><div class="side-head"><h1>拼写任务图文筛查</h1><div class="stats" id="stats"></div><div class="filters" id="filters"></div></div><div class="list" id="list"></div></aside><main><div class="header" id="content"></div></main></div><script src="data.js"></script><script>
const D=window.AUDIT_DATA;let filter='all',selected=D.episodes[0]?.episode_id,sub=0;
const labels={all:'全部',normal:'正常',annotation_anomaly:'标注异常',task_not_met:'任务未满足',uncertain:'不确定',consistent:'一致',inconsistent:'不一致',met:'标准满足',not_met:'标准未满足',needs_visual:'需要看图',text_only:'仅文本审核',targeted_visual_resolution:'定向图像复核'};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const badge=x=>`<span class="badge ${esc(x)}">${esc(labels[x]||x)}</span>`;
function drawFilters(){const keys=['all','normal','annotation_anomaly','task_not_met','uncertain'];document.querySelector('#filters').innerHTML=keys.map(k=>`<button class="${filter===k?'active':''}" onclick="setFilter('${k}')">${labels[k]}</button>`).join('')}
function setFilter(x){filter=x;const arr=shown();if(!arr.some(e=>e.episode_id===selected))selected=arr[0]?.episode_id;sub=0;draw()}
function shown(){return D.episodes.filter(e=>filter==='all'||e.output.review_status===filter)}
function drawList(){document.querySelector('#list').innerHTML=shown().map(e=>`<button class="ep ${e.episode_id===selected?'active':''}" onclick="pick('${e.episode_id}')"><div class="ep-id">${esc(e.episode_id)}</div>${badge(e.output.review_status)}</button>`).join('')}
function pick(id){selected=id;sub=0;draw()}
function pickSub(i){sub=i;drawContent()}
function textBlock(title,text){return `<div class="block"><h3>${esc(title)}</h3><p>${esc(text||'无')}</p></div>`}
function drawContent(){const e=D.episodes.find(x=>x.episode_id===selected);if(!e){document.querySelector('#content').innerHTML='<p>无结果</p>';return}const o=e.output,p=e.payload,subs=p.subtasks||p.final_annotation?.subtasks||[],reviews=o.subtask_reviews||[],r=reviews.find(x=>x.subtask_index===sub)||reviews[sub]||{},s=subs[sub]||{},r1=e.round1,r2=e.round2,resolutions=(r2?.resolutions||[]).filter(x=>String(x.subtask_index)===String(r.subtask_index));
const issues=(o.issues||[]).length?`<div class="issues"><h3>异常问题</h3>${o.issues.map((x,i)=>`<div class="issue"><strong>${i+1}. ${esc(x.issue_type)} · ${esc(x.certainty)}</strong><p>${esc(x.reason)}</p><div class="muted">重标范围：${esc(x.reannotation_scope)}　涉及段：${esc((x.affected_subtasks||[]).join(', '))}</div></div>`).join('')}</div>`:'';
const screening=r1?`<div class="summary"><div class="status-row">${badge(r1.review_status)}${badge(e.audit_route)}</div><p><strong>第一轮纯文本结论：</strong>${esc(r1.reason)}</p><p><strong>视觉请求：</strong>${esc((r1.visual_requests||[]).map(x=>'Subtask '+x.subtask_index+'：'+x.question).join('；')||'无')}</p></div>`:'';
const tabs=subs.map((x,i)=>`<button class="${sub===i?'active':''}" onclick="pickSub(${i})">Subtask ${i}</button>`).join('');
let evidence=resolutions.flatMap(x=>x.visual_evidence||[]);if(!evidence.length)evidence=e.frames.filter(f=>(f.references||[]).some(x=>String(x.subtask_index)===String(r.subtask_index??sub))).map(f=>({frame_ref:f.frame_ref,observation:(f.references||[]).filter(x=>String(x.subtask_index)===String(r.subtask_index??sub)).map(x=>x.role).join(', ')}));
const ev=evidence.map(v=>{const f=e.frames.find(x=>x.frame_ref===v.frame_ref);return `<div class="frame">${f?`<img src="${esc(f.asset)}" loading="lazy">`:''}<div class="cap"><strong>${esc(v.frame_ref)}${f?.timestamp_sec!=null?' · '+esc(f.timestamp_sec)+'s':''}</strong>${esc(v.observation)}</div></div>`}).join('');
const textEv=(r.text_evidence||[]).map(x=>`<div class="evidence"><strong>${esc(x.source)} ${esc(x.path)}</strong><br>${esc(x.quote)}</div>`).join('');const visualReview=resolutions.map(x=>`<div class="evidence"><strong>${badge(x.annotation_visual_status)} ${badge(x.criterion_status)}</strong><br>${esc(x.question)}<br>${esc(x.reason)}</div>`).join('');
document.querySelector('#content').innerHTML=`<h2>${esc(e.episode_id)}</h2><div class="muted">${esc(p.task)}</div>${screening}<div class="summary"><div class="status-row">${badge(o.review_status)}${badge(o.annotation_visual_status)}${badge(o.criterion_status)}</div><p><strong>最终结论：</strong>${esc(o.reason)}</p><p><strong>人工审核标准：</strong>${esc(p.review_standard)}</p></div>${issues}<div class="tabs">${tabs}</div><div class="panel"><div class="grid"><div>${textBlock('原始 segment_summary',s.segment_summary)}${textBlock('arm_1',s.arm_1?.description)}${textBlock('arm_2',s.arm_2?.description)}</div><div><div class="block"><h3>第一轮逐段文本审核</h3><div class="status-row">${badge(r.text_status||'')}</div><p><strong>核查声明：</strong>${esc(r.claim_under_review)}</p><p>${esc(r.reason)}</p>${textEv}${r.visual_question?`<p><strong>视觉问题：</strong>${esc(r.visual_question)}</p>`:''}</div>${visualReview?`<div class="block"><h3>第二轮定向视觉结论</h3>${visualReview}</div>`:''}<h3>仅在需要视觉裁决时输入的帧</h3><div class="gallery">${ev||'<p>该段由纯文本确定，没有向第二轮发送图像。</p>'}</div></div></div></div>`}
function draw(){drawFilters();drawList();drawContent();document.querySelector('#stats').textContent=`${D.episodes.length} 条 · ${D.total_images} 张输入帧`}draw();
</script></body></html>'''


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def export_episode(episode_dir: Path, assets_root: Path) -> dict[str, Any]:
    two_round = (episode_dir / "final.json").is_file() and (episode_dir / "round1/input.json").is_file()
    if two_round:
        wrapper = load_json(episode_dir / "final.json")
        detail = wrapper.get("round2")
        input_data = load_json(episode_dir / ("round2/input.json" if detail else "round1/input.json"))
        output = {
            "review_status": wrapper["final_status"],
            "annotation_visual_status": wrapper.get("annotation_status"),
            "criterion_status": wrapper.get("criterion_status"),
            "reason": wrapper["round1"]["reason"],
            "subtask_reviews": wrapper["round1"].get("subtask_reviews", []),
            "issues": [],
        }
    else:
        wrapper = None
        input_data = load_json(episode_dir / "input.json")
        output = load_json(episode_dir / "output.json")
    payload = input_data["payload"]
    source = Path(input_data["sources"]["keyframe_annotation"]).parent
    episode_assets = assets_root / episode_dir.name
    episode_assets.mkdir(parents=True, exist_ok=True)
    frames = []
    for item in payload.get("visual_frames", []):
        index = int(item["frame_index"])
        src = source / "stage4_keyframes" / "frames" / f"{index:08d}.jpg"
        dst = episode_assets / src.name
        if not src.is_file():
            raise FileNotFoundError(src)
        if not dst.exists():
            shutil.copy2(src, dst)
        frames.append({**item, "asset": f"assets/{episode_dir.name}/{dst.name}"})
    return {
        "episode_id": episode_dir.name,
        "payload": payload,
        "output": output,
        "round1": wrapper.get("round1") if wrapper else None,
        "round2": wrapper.get("round2") if wrapper else None,
        "audit_route": wrapper.get("audit_route") if wrapper else "single_round",
        "frames": frames,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a self-contained audit viewer.")
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    assets_root = output_dir / "assets"
    episodes = [
        export_episode(path, assets_root)
        for path in sorted(args.run_root.glob("robodojo_episode_*"))
        if ((path / "input.json").is_file() and (path / "output.json").is_file())
        or ((path / "final.json").is_file() and (path / "round1/input.json").is_file())
    ]
    data = {"episodes": episodes, "total_images": sum(len(x["frames"]) for x in episodes)}
    (output_dir / "data.js").write_text(
        "window.AUDIT_DATA=" + json.dumps(data, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    (output_dir / "index.html").write_text(HTML, encoding="utf-8")
    print(json.dumps({"episodes": len(episodes), "images": data["total_images"], "output": str(output_dir)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
