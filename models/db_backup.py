import os
import datetime
import time
import shutil
import json
import tempfile
import base64
import subprocess

from odoo import models, fields, api, tools, _
from odoo.exceptions import Warning, AccessDenied, UserError, ValidationError
import odoo

import logging
_logger = logging.getLogger(__name__)


class DbBackup(models.Model):
    _name = 'db.backup'
    _description = 'Backup configuration record'
    _inactive_visibility = 'visible'

    name = fields.Char(string='Nama')
    file_data = fields.Binary(string='File Download')
    file_name = fields.Char(string='Nama File')

    def _get_db_name(self):
        return self._cr.dbname

    name = fields.Char('Database', required=True, help='Database yang akan dibackup secara terjadwal',
                       default=_get_db_name)
    folder = fields.Char('Backup Directory', help='Path absolut untuk menyimpan backup', required=True,
                         default='/home/administrator/backup/')
    backup_type = fields.Selection([('zip', 'Zip'), ('dump', 'Dump')], 'Backup Type', required=True, default='zip')
    autoremove = fields.Boolean('Auto. Remove Backups')
    days_to_keep = fields.Integer('Hapus setelah x hari', required=True)
    active = fields.Boolean(string='Aktif', default=True, help='Tentukan apakah backup ini akan dijalankan oleh otomatisasi')

    scp_user = fields.Char(string='SCP User')
    scp_host = fields.Char(string='SCP Host')
    scp_path = fields.Char(string='SCP Path')
    scp_private_key = fields.Char(string='SCP Private Key Path')

    @api.model
    def create(self, vals):
        if vals.get('active'):
            self.search([]).write({'active': False})
        return super(DbBackup, self).create(vals)

    def write(self, vals):
        result = super(DbBackup, self).write(vals)
        if vals.get('active') and self.ids:
            self.search([('id', 'not in', self.ids)]).write({'active': False})
        return result

    def action_backup_now(self):
        self.ensure_one()
        if not os.path.isdir(self.folder):
            os.makedirs(self.folder)

        bkp_file = '%s_%s.%s' % (time.strftime('%Y_%m_%d_%H_%M_%S'), self.name, self.backup_type)
        file_path = os.path.join(self.folder, bkp_file)

        try:
            with open(file_path, 'wb') as fp:
                self._take_dump(self.name, fp, 'db.backup', self.backup_type)

            with open(file_path, "rb") as f:
                file_data = f.read()

            self.write({
                'file_data': base64.b64encode(file_data),
                'file_name': bkp_file,
            })

            return {
                'type': 'ir.actions.act_url',
                'url': f'/web/content?model={self._name}&id={self.id}&field=file_data&filename_field=file_name&download=true',
                'target': 'self',
            }
        except Exception as error:
            _logger.error("Error while doing manual backup: %s", str(error))
            raise UserError("Gagal melakukan backup sekarang: %s" % str(error))

    def action_send_scp_only(self):
        self.ensure_one()

        if not self.file_name:
            raise UserError("Tidak ada file backup yang tersedia untuk dikirim.")

        file_path = os.path.join(self.folder, self.file_name)
        if not os.path.isfile(file_path):
            raise UserError(f"File tidak ditemukan di path: {file_path}")

        if self.scp_user and self.scp_host and self.scp_path and self.scp_private_key:
            try:
                subprocess.run([
                    'scp', '-i', self.scp_private_key,
                    file_path,
                    f"{self.scp_user}@{self.scp_host}:{self.scp_path}"
                ], check=True)
            except subprocess.CalledProcessError as e:
                _logger.error(f"SCP transfer failed: {e}")
                raise UserError(f"Gagal mengirim file lewat SCP: {e}")
        else:
            raise UserError("Konfigurasi SCP belum lengkap.")

    @api.model
    def schedule_backup(self):
        conf_ids = self.search([('active', '=', True)])
        for rec in conf_ids:
            try:
                if not os.path.isdir(rec.folder):
                    os.makedirs(rec.folder)
            except Exception as e:
                _logger.error(f"Gagal membuat direktori backup: {e}")
                continue

            bkp_file = '%s_%s.%s' % (time.strftime('%Y_%m_%d_%H_%M_%S'), rec.name, rec.backup_type)
            file_path = os.path.join(rec.folder, bkp_file)

            try:
                with open(file_path, 'wb') as fp:
                    self._take_dump(rec.name, fp, 'db.backup', rec.backup_type)
            except Exception as error:
                _logger.debug("Gagal backup database %s.", rec.name)
                _logger.debug("Error: %s", str(error))
                continue

            if rec.autoremove:
                directory = rec.folder
                for f in os.listdir(directory):
                    fullpath = os.path.join(directory, f)
                    if rec.name in fullpath:
                        try:
                            timestamp = os.stat(fullpath).st_ctime
                            createtime = datetime.datetime.fromtimestamp(timestamp)
                            now = datetime.datetime.now()
                            delta = now - createtime
                            if delta.days >= rec.days_to_keep:
                                if os.path.isfile(fullpath) and (".dump" in f or '.zip' in f):
                                    _logger.info("Menghapus file lama: %s", fullpath)
                                    os.remove(fullpath)
                        except Exception as e:
                            _logger.warning(f"Gagal menghapus file: {e}")

    def _take_dump(self, db_name, stream, model, backup_format='zip'):
        cron_user_id = self.env.ref('backup-sftp.backup_scheduler').user_id.id
        if self._name != 'db.backup' or (self.env.user.id != cron_user_id and not self.env.user.has_group('base.group_system')):
            _logger.error('Unauthorized database operation. Backups should only be available from the cron job.')
            raise AccessDenied()

        _logger.info('DUMP DB: %s format %s', db_name, backup_format)

        cmd = ['pg_dump', '--no-owner']
        cmd.append(db_name)

        if backup_format == 'zip':
            with tempfile.TemporaryDirectory() as dump_dir:
                filestore = odoo.tools.config.filestore(db_name)
                if os.path.exists(filestore):
                    shutil.copytree(filestore, os.path.join(dump_dir, 'filestore'))
                with open(os.path.join(dump_dir, 'manifest.json'), 'w') as fh:
                    db = odoo.sql_db.db_connect(db_name)
                    with db.cursor() as cr:
                        json.dump(self._dump_db_manifest(cr), fh, indent=4)
                cmd.insert(-1, '--file=' + os.path.join(dump_dir, 'dump.sql'))
                odoo.tools.exec_pg_command(*cmd)
                if stream:
                    odoo.tools.osutil.zip_dir(dump_dir, stream, include_dir=False, fnct_sort=lambda file_name: file_name != 'dump.sql')
        else:
            cmd.insert(-1, '--format=c')
            stdin, stdout = odoo.tools.exec_pg_command_pipe(*cmd)
            if stream:
                shutil.copyfileobj(stdout, stream)

    def _dump_db_manifest(self, cr):
        pg_version = "%d.%d" % divmod(cr._obj.connection.server_version / 100, 100)
        cr.execute("SELECT name, latest_version FROM ir_module_module WHERE state = 'installed'")
        modules = dict(cr.fetchall())
        manifest = {
            'odoo_dump': '1',
            'db_name': cr.dbname,
            'version': odoo.release.version,
            'version_info': odoo.release.version_info,
            'major_version': odoo.release.major_version,
            'pg_version': pg_version,
            'modules': modules,
        }
        return manifest

    def action_generate_file(self):
        file_path = "/tmp/sftp_hasil_export.zip"
        try:
            with open(file_path, "rb") as f:
                file_data = f.read()

            self.write({
                'file_data': base64.b64encode(file_data),
                'file_name': 'sftp_export.zip',
            })
        except Exception as e:
            raise UserError(f"Gagal membaca file: {e}")

        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content?model={self._name}&id={self.id}&field=file_data&filename_field=file_name&download=true',
            'target': 'self',
        }

    @api.onchange('active')
    def _onchange_active(self):
        if self.active and self.id:
            others = self.search([('id', '!=', self.id), ('active', '=', True)])
            for record in others:
                record.active = False

    @api.constrains('active')
    def _check_only_one_active(self):
        for rec in self:
            if rec.active and rec.id:
                other_active = self.search([
                    ('id', '!=', rec.id),
                    ('active', '=', True)
                ], limit=1)
                if other_active:
                    raise ValidationError("Hanya satu backup yang boleh aktif dalam satu waktu. Nonaktifkan yang lain terlebih dahulu.")
