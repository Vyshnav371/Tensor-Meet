import {
  AfterViewInit,
  Component,
  ElementRef,
  OnDestroy,
  input,
  output,
  viewChild,
} from '@angular/core';

@Component({
  selector: 'tn-dialog',
  template: `<dialog
    #dialog
    class="modal"
    [attr.aria-label]="label()"
    (close)="closed.emit()"
    (click)="backdrop($event)"
  >
    <ng-content />
  </dialog>`,
})
export class DialogShell implements AfterViewInit, OnDestroy {
  readonly label = input('Dialog');
  readonly closed = output<void>();
  private dialog = viewChild.required<ElementRef<HTMLDialogElement>>('dialog');
  ngAfterViewInit() {
    this.dialog().nativeElement.showModal();
  }
  ngOnDestroy() {
    this.dialog().nativeElement.close();
  }
  backdrop(event: MouseEvent) {
    const dialog = this.dialog().nativeElement;
    const rect = dialog.getBoundingClientRect();
    if (
      event.target === dialog &&
      (event.clientX < rect.left ||
        event.clientX > rect.right ||
        event.clientY < rect.top ||
        event.clientY > rect.bottom)
    )
      dialog.close();
  }
}
