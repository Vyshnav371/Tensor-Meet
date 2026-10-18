import { Pipe, PipeTransform, inject } from '@angular/core';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import DOMPurify from 'dompurify';
import { marked } from 'marked';

/** Markdown -> sanitized HTML (tables, lists, links). Links open in a new tab. */
@Pipe({ name: 'markdown' })
export class MarkdownPipe implements PipeTransform {
  private sanitizer = inject(DomSanitizer);

  transform(value: string | null | undefined): SafeHtml {
    const html = marked.parse(value ?? '', { async: false, gfm: true, breaks: true }) as string;
    const clean = DOMPurify.sanitize(html, { ADD_ATTR: ['target'] }).replaceAll('<a ', '<a target="_blank" rel="noopener" ');
    return this.sanitizer.bypassSecurityTrustHtml(clean);
  }
}
