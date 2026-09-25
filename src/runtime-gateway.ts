import { readFile, writeFile, rename } from 'node:fs/promises';
import path from 'node:path';
/** Guest-side transport for Bench's offline hosted runtime. */
export class RuntimeGateway {
  private sequence = 0;
  constructor(private directory = process.env.BENCH_GATEWAY_PATH) {
    if (!directory) throw new Error('Bench gateway is available only inside a configured runtime');
  }
  async model(messages: Record<string,unknown>[], tools?: Record<string,unknown>[]): Promise<Record<string,unknown>> {
    const id=this.sequence++;
    const prefix=path.join(this.directory!,`request-${id}`);
    await writeFile(`${prefix}.tmp`,JSON.stringify({operation:'model',messages,...(tools?{tools}:{})}));
    await rename(`${prefix}.tmp`,`${prefix}.json`);
    const deadline=Date.now()+115000;
    while (Date.now()<deadline) {
      let raw: string;
      try { raw=await readFile(path.join(this.directory!,`response-${id}.json`),'utf8'); }
      catch(error) {
        if ((error as NodeJS.ErrnoException).code!=='ENOENT') throw error;
        await new Promise(resolve=>setTimeout(resolve,50)); continue;
      }
      const result=JSON.parse(raw);
      if (result.error) throw new Error(result.error);
      return result.message;
    }
    throw new Error('Bench gateway deadline exceeded');
  }
}
