import {useState} from 'react';
import {Button} from './components/ui/button';

type Value = {kind: 'text' | 'number' | 'yes' | 'no' | 'empty'; text: string};
const blank = (): Value => ({kind: 'text', text: ''});
function valueOf(value: Value) {
  switch (value.kind) {
    case 'text': return value.text;
    case 'yes': return true;
    case 'no': return false;
    case 'empty': return null;
    case 'number': {
      if (!value.text.trim() || !Number.isFinite(Number(value.text))) throw new Error('Enter a valid number.');
      const number = Number(value.text);
      if (Number.isInteger(number) && !Number.isSafeInteger(number)) throw new Error('Enter a smaller whole number.');
      return number;
    }
  }
}

function ExampleValue({label, value, change}: {label: string; value: Value; change: (value: Value) => void}) {
  return <fieldset className="example-value">
    <legend>{label}</legend>
    <select aria-label={`${label} type`} value={value.kind}
      onChange={e => change({kind: e.target.value as Value['kind'], text: ''})}>
      <option value="text">Text</option><option value="number">Number</option>
      <option value="yes">Yes (true)</option><option value="no">No (false)</option><option value="empty">No value</option>
    </select>
    {['text','number'].includes(value.kind) && <input aria-label={label} maxLength={512}
      type={value.kind === 'number' ? 'number' : 'text'} step="any" required={value.kind === 'number'}
      value={value.text} onChange={e => change({...value, text:e.target.value})}/>}
  </fieldset>;
}

// Convert the answers to the existing numbered clarification format. The same
// planner and review gates validate the submitted values and their meaning.
export function TaskExample({index, expectation, busy, send}: {
  index: number; expectation: string; busy: boolean; send: (answer: string) => void;
}) {
  const [inputs, setInputs] = useState<Value[]>([blank()]);
  const [expected, setExpected] = useState(blank);
  const [incorrect, setIncorrect] = useState(blank);
  const [error, setError] = useState('');
  return <form className="task-example" onSubmit={e => {
    e.preventDefault();
    setError('');
    try {
      const example = {args: inputs.map(valueOf), expected: valueOf(expected), incorrect: valueOf(incorrect)};
      if (example.expected === example.incorrect) throw new Error('Use two different results so the test can tell them apart.');
      const answer = `Example ${index}: ${JSON.stringify(example)}`;
      if ([...answer].length > 1600) throw new Error('Shorten this example so it fits in one reply.');
      send(answer);
    } catch (e) {
      setError((e as Error).message);
    }
  }}>
    <p><strong>{expectation}</strong></p>
    <p>Give one concrete example of what goes into the test, what should come out, and a wrong result the test must catch. For text, keep any spaces that matter.</p>
    {inputs.map((input, i) => <ExampleValue key={i} label={`Input ${i + 1}`} value={input}
      change={value => setInputs(previous => previous.map((entry, j) => i === j ? value : entry))}/>)}
    <div className="intake-actions">
      <Button type="button" variant="secondary" disabled={busy || inputs.length === 8} onClick={() => setInputs([...inputs, blank()])}>Add an input</Button>
      <Button type="button" variant="ghost" disabled={busy || !inputs.length} onClick={() => setInputs(inputs.slice(0, -1))}>Remove last input</Button>
    </div>
    {!inputs.length && <p>This example uses no inputs.</p>}
    <ExampleValue label="Expected result" value={expected} change={setExpected}/>
    <ExampleValue label="Wrong result to catch" value={incorrect} change={setIncorrect}/>
    {error && <p role="alert" className="form-error">{error}</p>}
    <Button disabled={busy}>Send example</Button>
  </form>;
}
