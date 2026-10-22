import { Directive, ElementRef, effect, inject, input } from '@angular/core';

/** Only text enters the DOM. KaTeX never receives trusted HTML or URL privileges. */
@Directive({ selector: '[mathText]' })
export class MathTextDirective {
  readonly mathText = input<string | null | undefined>('');
  readonly mathCode = input(false);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);

  constructor() {
    effect((onCleanup) => {
      let cancelled = false;
      onCleanup(() => { cancelled = true; });
      const element = this.host.nativeElement;
      const text = this.mathText() ?? '';
      element.replaceChildren();
      // Fenced/inline code is deliberately excluded from formula rendering.
      for (const part of text.split(/(```[\s\S]*?```|`[^`\n]+`)/g)) {
        if (part.startsWith('`')) {
          const code = document.createElement('code');
          code.textContent = part;
          element.appendChild(code);
        } else {
          element.appendChild(document.createTextNode(part));
        }
      }
      if (this.mathCode() || !/\$|\\[([]/.test(text)) return;
      void import('katex/contrib/auto-render').then(({ default: renderMathInElement }) => {
        if (cancelled) return;
        renderMathInElement(element, {
        delimiters: [
          { left: '$$', right: '$$', display: true },
          { left: '\\[', right: '\\]', display: true },
          { left: '\\(', right: '\\)', display: false },
          { left: '$', right: '$', display: false },
        ],
        throwOnError: false,
        trust: false,
        maxExpand: 500,
        maxSize: 20,
        strict: 'ignore',
        });
      }).catch(() => { /* Network/chunk errors leave the original readable formula intact. */ });
    });
  }
}
