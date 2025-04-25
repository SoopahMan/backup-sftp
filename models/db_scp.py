import os
import subprocess

from odoo import models, fields, api
from odoo.exceptions import UserError


class DbBackupSCPWizard(models.TransientModel):
    _name = 'db.backup.scp.wizard'
    _description = 'Wizard Kirim Backup via SCP'

    scp_user = fields.Char('SCP User', required=True)
    scp_host = fields.Char('SCP Host', required=True)
    scp_path = fields.Char('SCP Path', required=True)
    scp_private_key = fields.Char('Path ke Private Key', required=True)
    backup_ids = fields.Many2many('db.backup', string="Backup untuk dikirim")

    def action_send(self):
        for backup in self.backup_ids:
            file_path = os.path.join(backup.folder, backup.file_name)
            if not os.path.exists(file_path):
                raise UserError(f"File tidak ditemukan: {file_path}")
            try:
                subprocess.run([
                    'scp', '-i', self.scp_private_key,
                    file_path,
                    f"{self.scp_user}@{self.scp_host}:{self.scp_path}"
                ], check=True)
            except subprocess.CalledProcessError as e:
                raise UserError(f"Gagal mengirim file: {e}")
