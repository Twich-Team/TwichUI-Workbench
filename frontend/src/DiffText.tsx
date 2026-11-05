import {Fragment, type ReactNode} from 'react';

type LineKind = 'context' | 'metadata' | 'hunk' | 'added' | 'removed';

export function diffLines(text:string) {
  let inHunk=false;
  return (text.match(/[^\n]*\n|[^\n]+$/g)||[]).map(raw=>{
    const line=raw.endsWith('\n')?raw.slice(0,-1):raw;
    let kind:LineKind='context';
    if(line.startsWith('diff --git ')) {inHunk=false;kind='metadata';}
    else if(line.startsWith('@@')) {inHunk=true;kind='hunk';}
    else if(!inHunk && /^(--- |\+\+\+ |index |new file |deleted file |old mode |new mode |rename |copy |similarity |dissimilarity |Binary files )/.test(line)) kind='metadata';
    else if(line.startsWith('\\')) kind='metadata';
    else if(inHunk && line.startsWith('+')) kind='added';
    else if(inHunk && line.startsWith('-')) kind='removed';
    return {line,kind,newline:raw.endsWith('\n')};
  });
}

// Bounded lexical decoration only: not a JSON parser, validator or HTML highlighter.
function jsonText(text:string) {
  const pattern=/"(?:\\.|[^"\\])*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\b(?:true|false|null)\b|[{}\[\]:,]/g;
  const nodes:ReactNode[]=[];
  let end=0;
  for(const match of text.matchAll(pattern)) {
    const at=match.index;
    nodes.push(text.slice(end,at));
    const token=match[0];
    const kind=token.startsWith('"')
      ? (/^\s*:/.test(text.slice(at+token.length))?'key':'string')
      : /^-?\d/.test(token)?'number'
      : /^(true|false|null)$/.test(token)?'literal':'punctuation';
    nodes.push(<span className={`json-${kind}`} key={at}>{token}</span>);
    end=at+token.length;
  }
  nodes.push(text.slice(end));
  return nodes;
}

export function DiffText({text,path}:{text:string;path:string}) {
  const json=/\.json$/i.test(path);
  return <>{diffLines(text).map(({line,kind,newline},i)=>{
    const code=kind==='context'||kind==='added'||kind==='removed';
    const prefix=code && /^[ +\-]/.test(line)?line.slice(0,1):'';
    return <Fragment key={i}>
      <span className={`diff-line diff-${kind}`}>
        {json&&code?<>{prefix}{jsonText(line.slice(prefix.length))}</>:line}
      </span>{newline?'\n':''}
    </Fragment>;
  })}</>;
}
