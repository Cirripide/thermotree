import { Component, inject } from '@angular/core';
import { MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatIconModule } from '@angular/material/icon';

@Component({
  selector: 'app-about-dialog',
  imports: [MatDialogModule, MatIconModule],
  templateUrl: './about-dialog.component.html',
  styleUrl: './about-dialog.component.scss',
})
export class AboutDialogComponent {
  private readonly dialogRef = inject(MatDialogRef<AboutDialogComponent>);

  close(): void {
    this.dialogRef.close();
  }
}
